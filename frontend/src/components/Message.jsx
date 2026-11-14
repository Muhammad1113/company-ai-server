import { memo, useCallback, useMemo, useState } from 'react';
import Markdown from './Markdown.jsx';
import Sources from './Sources.jsx';
import { copyText } from '../lib/clipboard.js';
import { isNotFound } from '../lib/sources.js';
import { answerOrigin, cleanAnswer, dropGeneralLabel } from '../lib/answer.js';
import { getFeedback, setFeedback } from '../lib/feedback.js';

function Dots() {
  return <span className="dots" aria-label="Thinking"><i /><i /><i /></span>;
}

function Thumb({ up }) {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={up ? undefined : { transform: 'rotate(180deg)' }}>
      <path d="M5 7v6H2.5V7zM5 7l3-5c1.2 0 1.8.9 1.5 2L9 6h3.5a1 1 0 0 1 1 1.2l-.9 4.8a1.5 1.5 0 0 1-1.5 1H5" />
    </svg>
  );
}

const ORIGIN = {
  tool: 'Calculated by the server (exact code, not by the AI model)',
  docs: 'Based on company documents',
  docgen: 'Based on company documents',
  general: 'General knowledge, not from company documents',
  web: 'From the internet, not from company documents',
  mixed: 'From company documents and the internet',
};

const DRAFT = 'Write a short, polite email to the buyer using only the facts in your last answer.';

function Message({ msg, live = false, isLast = false, canRegenerate = false, onRegenerate, onAsk }) {
  const [fb, setFb] = useState(() => getFeedback(msg.id));
  const vote = (v) => { const next = fb === v ? 0 : v; setFb(next); setFeedback(msg.id, next); };
  const domId = `m-${msg.id || 'live'}`;
  const [srcOpen, setSrcOpen] = useState(false);
  const [hl, setHl] = useState(null);
  const [copied, setCopied] = useState(false);
  const cleaned = useMemo(() => cleanAnswer(msg.content), [msg.content]);
  const origin = useMemo(() => (isNotFound(cleaned) ? null : answerOrigin(cleaned, msg.sources)), [cleaned, msg.sources]);
  const text = useMemo(() => dropGeneralLabel(cleaned), [cleaned]);

  const onCite = useCallback((n) => {
    setSrcOpen(true);
    setHl(n);
    setTimeout(() => document.getElementById(`${domId}-src-${n}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 30);
  }, [domId]);

  if (msg.role === 'user') {
    return (
      <div className="msg msg-user">
        <div className="bubble" dir="auto">{msg.content}</div>
      </div>
    );
  }

  const waiting = live && !msg.content && msg.status === 'streaming';
  const onCopy = async () => {
    if (await copyText(text)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  return (
    <div className="msg msg-assistant">
      <div className="avatar ai" aria-hidden="true" />
      <div className="msg-main">
        {origin && !waiting && <p className={`origin origin-${origin}`}>{ORIGIN[origin]}</p>}
        {waiting ? <Dots /> : <Markdown text={text} sources={msg.sources} onCite={onCite} />}
        {live && msg.content && msg.status === 'streaming' ? <span className="caret" aria-hidden="true" /> : null}

        {msg.status === 'stopped' && <p className="note">Stopped. This answer is incomplete.</p>}
        {msg.status === 'error' && (
          <p className="note note-error">
            {msg.errorDetail || 'Generation failed.'} {msg.content ? 'The answer above is incomplete.' : ''}
          </p>
        )}

        {!live && origin === 'web' && (
          <p className="verify">Internet answer. Open the linked websites to confirm before you rely on it.</p>
        )}

        <Sources
          domId={domId}
          sources={msg.sources}
          content={text}
          streaming={live && msg.status === 'streaming'}
          open={srcOpen}
          onToggle={() => setSrcOpen((o) => !o)}
          highlight={hl}
        />

        {!live && (
          <div className="msg-actions">
            {msg.content ? (
              <button type="button" className="link-btn" onClick={onCopy}>{copied ? 'Copied' : 'Copy'}</button>
            ) : null}
            {msg.id && msg.content ? (
              <>
                <button type="button" className={`icon-btn thumb ${fb === 1 ? 'on' : ''}`} onClick={() => vote(1)} aria-label="Good answer" aria-pressed={fb === 1}><Thumb up /></button>
                <button type="button" className={`icon-btn thumb ${fb === -1 ? 'on bad' : ''}`} onClick={() => vote(-1)} aria-label="Bad answer" aria-pressed={fb === -1}><Thumb /></button>
              </>
            ) : null}
            {isLast && canRegenerate && onAsk && msg.content ? (
              <button type="button" className="link-btn" onClick={() => onAsk(DRAFT)}>Draft buyer email</button>
            ) : null}
            {isLast && canRegenerate ? (
              <button type="button" className="link-btn" onClick={onRegenerate}>Regenerate</button>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}

export default memo(Message);
