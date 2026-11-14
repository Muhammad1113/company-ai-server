import { useCallback, useEffect, useRef, useState } from 'react';
import Message from './Message.jsx';
import Composer from './Composer.jsx';
import Orb from './Orb.jsx';
import { useToast } from '../context/Toast.jsx';
import { toMarkdown, download } from '../lib/exportChat.js';
import { copyText } from '../lib/clipboard.js';
import { displayName } from '../lib/format.js';
import { STARTERS } from '../lib/starters.js';

const I = ({ d, size = 18 }) => (
  <svg viewBox="0 0 20 20" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={d} />
  </svg>
);
const P = {
  menu: 'M3 5.5h14M3 10h14M3 14.5h14',
  panel: 'M3 4.5A1.5 1.5 0 0 1 4.5 3h11A1.5 1.5 0 0 1 17 4.5v11a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 3 15.5zM8 3v14',
  dots: 'M4.5 10h.01M10 10h.01M15.5 10h.01',
  link: 'M8.5 11.5a3.2 3.2 0 0 0 4.5 0l2.5-2.5a3.2 3.2 0 0 0-4.5-4.5l-.9.9M11.5 8.5a3.2 3.2 0 0 0-4.5 0L4.5 11a3.2 3.2 0 0 0 4.5 4.5l.9-.9',
  down: 'M10 3.5v9M6 9l4 4 4-4M4 16.5h12',
  file: 'M6 2.5h5.5L15 6v11a.5.5 0 0 1-.5.5h-8.5a.5.5 0 0 1-.5-.5v-14a.5.5 0 0 1 .5-.5zM11.5 2.5V6H15',
  plus: 'M10 4v12M4 10h12',
  trash: 'M4 5.5h12M7.5 5.5V4h5v1.5M5.5 5.5l.7 10.5h7.6l.7-10.5',
  shield: 'M10 2.5 16 5v4.5c0 3.6-2.4 6.3-6 8-3.6-1.7-6-4.4-6-8V5z',
  tag: 'M3 3h6.5l7 7-6.5 6.5-7-7zM6.5 6.5h.01',
  book: 'M4 3.5h9a2 2 0 0 1 2 2v11H6a2 2 0 0 1-2-2zM4 14.5a2 2 0 0 1 2-2h9',
  arrow: 'M6 14 14 6M7.5 6H14v6.5',
  globe: 'M10 17.5a7.5 7.5 0 1 0 0-15 7.5 7.5 0 0 0 0 15zM2.5 10h15M10 2.5c2 2.1 3 4.6 3 7.5s-1 5.4-3 7.5c-2-2.1-3-4.6-3-7.5s1-5.4 3-7.5z',
  box: 'M3 6.5 10 3l7 3.5v7L10 17l-7-3.5zM3 6.5 10 10l7-3.5M10 10v7',
};

export default function ChatView({ chat, collection, onMenu, onExpand, title, user, onNew, onDelete, onCalc }) {
  const { messages, stream, loading, restore, send, stop } = chat;
  const { push } = useToast();
  const scroller = useRef(null);
  const stick = useRef(true);
  const [pop, setPop] = useState(null);       // 'more' | 'export' | null

  const onScroll = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }, []);

  // Nayi conversation => neeche; streaming mein sirf tab follow karo jab user neeche ho
  useEffect(() => { stick.current = true; }, [chat.activeId]);
  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages, stream, loading]);

  useEffect(() => {
    if (!pop) return undefined;
    const close = (e) => { if (!e.target.closest('.menu-wrap')) setPop(null); };
    const esc = (e) => { if (e.key === 'Escape') setPop(null); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc); };
  }, [pop]);

  const streaming = !!stream;
  const lastIsAssistant = messages.length > 0 && messages[messages.length - 1].role === 'assistant';
  const empty = !messages.length && !stream && !loading;
  const hasMessages = messages.length > 0;

  const livedMsg = stream && {
    id: 'live', role: 'assistant', content: stream.text, sources: stream.sources,
    status: stream.status, errorDetail: stream.error,
  };

  const fileBase = `${(title || 'chat').replace(/[^\w-]+/g, '_').slice(0, 40)}`;
  const savePdf = () => { setPop(null); window.print(); };
  const saveMd = () => { setPop(null); download(`${fileBase}.md`, toMarkdown(messages, title)); };
  const copyChat = async () => {
    setPop(null);
    if (!hasMessages) return;
    push((await copyText(toMarkdown(messages, title))) ? 'Conversation copied.' : 'Could not copy.', 'success');
  };

  return (
    <section className={`chat ${empty ? 'is-empty' : ''}`}>
      <header className="topbar">
        <button type="button" className="round-btn menu-btn" onClick={onMenu} aria-label="Open conversations"><I d={P.menu} /></button>
        <button type="button" className="round-btn expand-btn" onClick={onExpand} aria-label="Show sidebar" title="Show sidebar"><I d={P.panel} /></button>
        <span className="model-chip"><img src="/logo.jpg" alt="" />MetaXpert <small>AI</small></span>
        {hasMessages ? <span className="topbar-title" dir="auto" title={title}>{title}</span> : <span className="topbar-spacer" />}

        <div className="topbar-actions">
          <div className="menu-wrap">
            <button type="button" className="round-btn" onClick={() => setPop(pop === 'more' ? null : 'more')} aria-label="More" aria-haspopup="menu" aria-expanded={pop === 'more'}><I d={P.dots} /></button>
            {pop === 'more' && (
              <div className="pop" role="menu">
                <button type="button" role="menuitem" onClick={() => { setPop(null); onNew(); }}><I d={P.plus} size={16} />New chat</button>
                <button type="button" role="menuitem" className="danger" disabled={!chat.activeId} onClick={() => { setPop(null); onDelete(); }}><I d={P.trash} size={16} />Delete chat</button>
              </div>
            )}
          </div>
          <button type="button" className="round-btn" onClick={copyChat} disabled={!hasMessages} aria-label="Copy conversation" title="Copy conversation"><I d={P.link} /></button>
          <div className="menu-wrap">
            <button type="button" className="outline-btn" onClick={() => setPop(pop === 'export' ? null : 'export')} disabled={!hasMessages} aria-haspopup="menu" aria-expanded={pop === 'export'}>
              <I d={P.down} size={16} /><span className="export-label">Export chat</span>
            </button>
            {pop === 'export' && (
              <div className="pop" role="menu">
                <button type="button" role="menuitem" onClick={savePdf}><I d={P.file} size={16} />Save as PDF</button>
                <button type="button" role="menuitem" onClick={saveMd}><I d={P.file} size={16} />Download Markdown</button>
              </div>
            )}
          </div>
        </div>
      </header>

      <div className="stage">
        <div className="orb-box"><Orb /></div>
        <p className="greet" dir="auto">Hello, {displayName(user?.username)}</p>
        <h1 className="greet-q">How can I assist you today?</h1>
      </div>

      <div className="scroll" ref={scroller} onScroll={onScroll}>
        <div className="thread">
          {loading && <p className="muted center">Loading conversation…</p>}
          {messages.map((m, i) => (
            <Message
              key={m.id || i}
              msg={m}
              isLast={i === messages.length - 1 && lastIsAssistant}
              canRegenerate={!streaming}
              onAsk={collection ? (t) => send({ text: t, collection }) : undefined}
              onRegenerate={() => send({ regenerate: true, collection })}
            />
          ))}
          {livedMsg && <Message key="live" msg={livedMsg} live />}
        </div>
      </div>

      {chat.waitNote && <p className="busy-note" role="status">{chat.waitNote}</p>}

      <div className="dock">
        <Composer
          streaming={streaming}
          onSend={(text) => send({ text, collection })}
          onStop={stop}
          restore={restore}
          onCalc={onCalc}
        />
        {empty && (
          <div className="cards">
            {STARTERS.map((s) => (
              <button key={s.title} type="button" className="card" onClick={() => send({ text: s.ask, collection })}>
                <span className="card-ic"><I d={P[s.icon]} size={16} /></span>
                <span className="card-txt"><strong>{s.title}</strong><span>{s.hint}</span></span>
                <span className="card-arrow"><I d={P.arrow} size={16} /></span>
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
