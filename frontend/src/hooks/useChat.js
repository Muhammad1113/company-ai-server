import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../lib/api.js';
import { api, describeError } from '../lib/services.js';
import { dropLastAssistant, initialStream, reduceStream } from '../lib/chatState.js';

// Lambi quotation/spec tables ke liye .env mein VITE_MAX_TOKENS=1200 rakh sakte hain (server ka default 300 hai)
const MAX_TOKENS = Number(import.meta.env?.VITE_MAX_TOKENS) || 700;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const schedule = (fn) =>
  (typeof requestAnimationFrame === 'function' ? requestAnimationFrame(fn) : setTimeout(fn, 16));

/**
 * Ek active conversation + uska streaming jawab.
 * Server hi source of truth hai: stream khatam hone par conversation dobara load hoti hai.
 */
export function useChat({ onListChanged, notify }) {
  const [activeId, setActiveId] = useState(null);
  const [messages, setMessagesState] = useState([]);
  const [stream, setStream] = useState(null);       // null => koi jawab nahi aa raha
  const [loading, setLoading] = useState(false);
  const [restore, setRestore] = useState({ text: '', n: 0 });   // fail hone par composer mein wapas
  const [waitNote, setWaitNote] = useState('');                  // queue bhari ho to "busy, dobara koshish"

  const messagesRef = useRef([]);
  const activeRef = useRef(null);
  const runRef = useRef(null);
  const loadSeq = useRef(0);

  const setMessages = useCallback((next) => {
    messagesRef.current = next;
    setMessagesState(next);
  }, []);

  const abortRun = useCallback(() => {
    const run = runRef.current;
    runRef.current = null;
    if (run) run.ac.abort();
  }, []);

  useEffect(() => abortRun, [abortRun]);   // unmount par band

  const select = useCallback(async (id) => {
    abortRun();
    const seq = ++loadSeq.current;
    activeRef.current = id;
    setActiveId(id);
    setStream(null);
    if (!id) {
      setMessages([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const conv = await api.getConversation(id);
      if (seq === loadSeq.current) setMessages(conv.messages || []);
    } catch (err) {
      if (seq !== loadSeq.current) return;
      setMessages([]);
      if (err?.status === 404) { activeRef.current = null; setActiveId(null); onListChanged?.(); }
      if (err?.status !== 401) notify?.(describeError(err, 'Could not load this conversation'), 'error');
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, [abortRun, notify, onListChanged, setMessages]);

  const send = useCallback(async ({ text = '', regenerate = false, collection = '' }) => {
    if (runRef.current) return;
    const trimmed = text.trim();
    if (!regenerate && !trimmed) return;

    let id = activeRef.current;
    let created = false;
    if (!id) {
      try {
        const conv = await api.createConversation();
        id = conv.id;
        created = true;
        activeRef.current = id;
        loadSeq.current += 1;            // pending load ko ignore
        setActiveId(id);
        onListChanged?.();
      } catch (err) {
        if (err?.status !== 401) notify?.(describeError(err), 'error');
        if (!regenerate) setRestore((r) => ({ text: trimmed, n: r.n + 1 }));
        return;
      }
    }

    const run = { ac: new AbortController() };
    runRef.current = run;
    const before = messagesRef.current;
    setMessages(regenerate
      ? dropLastAssistant(before)
      : [...before, { id: `local-${Date.now()}`, role: 'user', content: trimmed, status: 'complete', sources: null }]);
    setStream(initialStream());

    // Tokens ko ek frame mein jama karke apply (har token par re-render nahi)
    let local = initialStream();
    let pending = [];
    let scheduled = false;
    const flush = () => {
      scheduled = false;
      if (!pending.length) return;
      const batch = pending;
      pending = [];
      if (runRef.current === run) setStream((s) => batch.reduce(reduceStream, s ?? initialStream()));
    };
    const onEvent = (ev) => {
      local = reduceStream(local, ev);
      pending.push(ev);
      if (ev.type === 'token') {
        if (!scheduled) { scheduled = true; schedule(flush); }
      } else flush();
    };

    const body = regenerate ? { regenerate: true } : { message: trimmed };
    if (collection) body.collection = collection;
    body.max_tokens = MAX_TOKENS;

    let aborted = false;
    let preStreamError = null;
    // Queue bhari (429 "queue is full"): 5 sec ruk kar do dafa tak khud dobara koshish.
    // "Aap ki pehli request abhi chal rahi hai" wali 429 par retry nahi.
    const streamWithRetry = async () => {
      for (let attempt = 0; ; attempt += 1) {
        try {
          await api.chatStream(id, body, { signal: run.ac.signal, onEvent });
          return;
        } catch (err) {
          const queueFull = err instanceof ApiError && err.status === 429 && /queue/i.test(err.detail || '');
          if (!queueFull || attempt >= 2 || run.ac.signal.aborted) throw err;
          setWaitNote('Server is busy right now. Trying again in a few seconds…');
          await sleep(Math.max(2, Number(err.retryAfter) || 5) * 1000);
          if (run.ac.signal.aborted) throw new DOMException('Aborted', 'AbortError');
        }
      }
    };

    try {
      await streamWithRetry();
      if (local.status === 'streaming') onEvent({ type: 'error', detail: 'The connection ended before the answer was complete.' });
    } catch (err) {
      if (err?.name === 'AbortError') { aborted = true; onEvent({ type: 'abort' }); }
      else if (err instanceof ApiError) preStreamError = err;     // HTTP status stream shuru hone se pehle
      else onEvent({ type: 'error', detail: 'The connection was lost while answering.' });
    }
    flush();
    setWaitNote('');

    const current = runRef.current === run;
    if (current) runRef.current = null;

    // 429/403/502... : kuch save nahi hua. Wapas karo.
    if (preStreamError) {
      if (current) {
        setStream(null);
        setMessages(before);
        if (!regenerate) setRestore((r) => ({ text: trimmed, n: r.n + 1 }));
        if (preStreamError.status !== 401) notify?.(describeError(preStreamError), 'error');
        if (created) {
          api.deleteConversation(id).catch(() => {});
          activeRef.current = null;
          setActiveId(null);
        }
      }
      onListChanged?.();
      return;
    }

    if (!current) { onListChanged?.(); return; }   // user kisi aur conversation par chala gaya

    // Server ka asli record. Stop ke baad server ko save karne ka thora waqt do.
    if (aborted) await sleep(450);
    const partial = {
      id: `local-assistant-${Date.now()}`,
      role: 'assistant',
      content: local.text,
      status: local.status === 'stopped' ? 'stopped' : local.status === 'error' ? 'error' : 'complete',
      sources: local.sources,
      errorDetail: local.error,
    };
    try {
      const conv = await api.getConversation(id);
      if (activeRef.current === id) {
        let list = conv.messages || [];
        const last = list[list.length - 1];
        if ((!last || last.role !== 'assistant') && (partial.content || partial.status === 'error')) list = [...list, partial];
        else if (last && last.role === 'assistant' && last.status === 'error' && local.error) list = [...list.slice(0, -1), { ...last, errorDetail: local.error }];
        setMessages(list);
      }
    } catch {
      if (activeRef.current === id && (partial.content || partial.status === 'error')) {
        setMessages([...messagesRef.current, partial]);
      }
    }
    if (activeRef.current === id) setStream(null);
    onListChanged?.();
  }, [notify, onListChanged, setMessages]);

  const stop = useCallback(() => {
    // run ko null mat karo: send() ka cleanup abort event handle karta hai
    runRef.current?.ac.abort();
  }, []);

  return { activeId, messages, stream, loading, restore, waitNote, select, send, stop };
}
