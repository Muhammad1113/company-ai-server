import { useEffect, useLayoutEffect, useRef, useState } from 'react';

const MAX = 12000;

export default function Composer({ streaming, onSend, onStop, restore, onCalc }) {
  const [text, setText] = useState('');
  const ref = useRef(null);

  useEffect(() => {
    if (restore.n) { setText(restore.text); ref.current?.focus(); }
  }, [restore.n]);        // eslint-disable-line react-hooks/exhaustive-deps

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [text]);

  function submit() {
    const value = text.trim();
    if (!value || streaming || value.length > MAX) return;
    onSend(value);
    setText('');
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  const over = text.length > MAX;

  return (
    <div className="composer-wrap">
      <div className="composer">
        <textarea
          ref={ref}
          dir="auto"
          rows={1}
          value={text}
          placeholder="Ask me anything…"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          aria-label="Message"
        />
        <div className="composer-bar">
          <div className="composer-tools">
            {onCalc && (
              <button type="button" className="tool-chip" onClick={onCalc}>
                <svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M5 2.5h10a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-13a1 1 0 0 1 1-1zM7 6h6M7 10h.01M10 10h.01M13 10h.01M7 13.5h.01M10 13.5h.01M13 13.5h.01" />
                </svg>
                Calculator
              </button>
            )}
            {over && <span className="over-note">{text.length - MAX} characters over the limit</span>}
          </div>
          {streaming ? (
            <button type="button" className="send stop" onClick={onStop} aria-label="Stop generating" title="Stop">
              <svg viewBox="0 0 16 16" width="14" height="14"><rect x="3" y="3" width="10" height="10" rx="2" fill="currentColor" /></svg>
            </button>
          ) : (
            <button type="button" className="send" onClick={submit} disabled={!text.trim() || over} aria-label="Send" title="Send (Enter)">
              <svg viewBox="0 0 16 16" width="16" height="16"><path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
