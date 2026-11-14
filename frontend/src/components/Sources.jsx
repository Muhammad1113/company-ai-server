import { useMemo, useState } from 'react';
import { clamp } from '../lib/format.js';
import { citingText, excerptWindow, highlightRanges } from '../lib/answer.js';
import { domainOf, groupByDoc, isWeb, planSources } from '../lib/sources.js';
import { safeUrl } from '../lib/markdown.js';

function Marked({ text, ranges, from, to }) {
  const out = [];
  let pos = from;
  ranges.forEach(([rs, re], i) => {
    const a = Math.max(rs, from);
    const b = Math.min(re, to);
    if (b <= a) return;
    if (a > pos) out.push(text.slice(pos, a));
    out.push(<mark key={i} className="pass-hl">{text.slice(a, b)}</mark>);
    pos = b;
  });
  if (pos < to) out.push(text.slice(pos, to));
  return <>{out}</>;
}

// Ek passage: page badge + jawab ke bayaan se milta jumla highlight (asal wajah jis se jawab bana)
function Passage({ s, domId, highlight, content }) {
  const [full, setFull] = useState(false);
  const text = String(s.preview || '');
  const ranges = useMemo(() => highlightRanges(text, citingText(content, s.ref)), [text, content, s.ref]);
  const [from, to] = full ? [0, text.length] : excerptWindow(text, ranges, 260);
  const long = text.length > 260;
  return (
    <li id={`${domId}-src-${s.ref}`} className={highlight === s.ref ? 'hl' : ''}>
      <span className="cite static">{s.ref}</span>
      <div className="src-body">
        <span className="page-chip">{s.page != null ? `Page ${s.page}` : `Part ${Number(s.chunk_index) + 1}`}</span>
        {text ? (
          <p dir="auto">
            {from > 0 ? '… ' : ''}<Marked text={text} ranges={ranges} from={from} to={to} />{to < text.length ? ' …' : ''}
          </p>
        ) : null}
        {long && <button type="button" className="link-btn" onClick={() => setFull((v) => !v)}>{full ? 'Show less' : 'Show full passage'}</button>}
      </div>
    </li>
  );
}

function Web({ s, domId, highlight }) {
  const href = safeUrl(s.url);
  const host = domainOf(s.url) || s.doc_id || 'website';
  return (
    <li id={`${domId}-src-${s.ref}`} className={`src-doc src-web ${highlight === s.ref ? 'hl' : ''}`}>
      <div className="src-doc-head">
        <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" strokeWidth="1.3" /><path d="M1.8 8h12.4M8 1.8c2 2 2 10.4 0 12.4M8 1.8c-2 2-2 10.4 0 12.4" fill="none" stroke="currentColor" strokeWidth="1.2" /></svg>
        <span className="cite static">{s.ref}</span>
        {href
          ? <a href={href} target="_blank" rel="noopener noreferrer nofollow" dir="auto">{clamp(String(s.title || host), 70)}</a>
          : <strong dir="auto">{clamp(String(s.title || host), 70)}</strong>}
        <span className="muted small src-host">{host}</span>
      </div>
      {s.preview ? <p className="src-snippet" dir="auto">{clamp(String(s.preview), 240)}</p> : null}
    </li>
  );
}

function pagesLabel(items) {
  const pages = [...new Set(items.map((i) => i.page).filter((p) => p != null))].sort((a, b) => a - b);
  if (!pages.length) return '';
  return pages.length === 1 ? `page ${pages[0]}` : `pages ${pages.join(', ')}`;
}

function Doc({ group, domId, highlight, content }) {
  return (
    <li className="src-doc">
      <div className="src-doc-head">
        <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><path d="M4 1.8h5l3 3V14H4zM9 1.8V5h3" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" /></svg>
        <strong dir="auto">{group.doc_id}</strong>
        <span className="muted small src-host">{pagesLabel(group.items)}</span>
      </div>
      <ul className="src-passages">
        {group.items.map((s) => <Passage key={s.ref} s={s} domId={domId} highlight={highlight} content={content} />)}
      </ul>
    </li>
  );
}

// Jawab ke neeche: sirf wahi sources jo jawab mein cite hue, doc ke hisaab se group.
export default function Sources({ domId, sources, content, streaming, open, onToggle, highlight }) {
  const [showMore, setShowMore] = useState(false);
  const plan = useMemo(() => planSources(sources, content, streaming), [sources, content, streaming]);
  const cited = useMemo(() => groupByDoc(plan.cited.filter((x) => !isWeb(x))), [plan]);
  const more = useMemo(() => groupByDoc(plan.more.filter((x) => !isWeb(x))), [plan]);
  const citedWeb = useMemo(() => plan.cited.filter(isWeb), [plan]);
  const moreWeb = useMemo(() => plan.more.filter(isWeb), [plan]);
  if (plan.hidden) return null;

  const docs = cited.map((g) => g.doc_id);
  let label;
  if (!docs.length) label = citedWeb.length === 1 ? 'Source: 1 website' : `Sources: ${citedWeb.length} websites`;
  else if (!citedWeb.length) label = docs.length === 1 ? `Source: ${clamp(docs[0], 38)}` : `Sources: ${docs.length} documents`;
  else label = `Sources: ${docs.length} ${docs.length === 1 ? 'document' : 'documents'} + ${citedWeb.length} ${citedWeb.length === 1 ? 'website' : 'websites'}`;
  const passages = plan.cited.length;

  return (
    <section className="sources" aria-label="Sources">
      <button type="button" className="sources-toggle" onClick={onToggle} aria-expanded={open}>
        <span className={`chev ${open ? 'open' : ''}`} aria-hidden="true">›</span>
        {label}
        <span className="muted"> · {passages} {passages === 1 ? 'passage' : 'passages'}</span>
      </button>
      {open && (
        <>
          <ul className="sources-list">
            {cited.map((g) => <Doc key={g.doc_id} group={g} domId={domId} highlight={highlight} content={content} />)}
            {citedWeb.map((w) => <Web key={w.ref} s={w} domId={domId} highlight={highlight} />)}
          </ul>
          {(more.length > 0 || moreWeb.length > 0) && (
            <>
              <button type="button" className="link-btn more-btn" onClick={() => setShowMore((v) => !v)}>
                {showMore ? 'Hide' : 'Show'} {plan.more.length} other retrieved {plan.more.length === 1 ? 'passage' : 'passages'}
              </button>
              {showMore && (
                <ul className="sources-list faded">
                  {more.map((g) => <Doc key={g.doc_id} group={g} domId={domId} highlight={highlight} content={content} />)}
                  {moreWeb.map((w) => <Web key={w.ref} s={w} domId={domId} highlight={highlight} />)}
                </ul>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
