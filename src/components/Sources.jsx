import { useMemo, useState } from 'react';
import { clamp, percent } from '../lib/format.js';
import { domainOf, groupByDoc, isWeb, planSources } from '../lib/sources.js';
import { safeUrl } from '../lib/markdown.js';

function Passage({ s, domId, highlight }) {
  const [full, setFull] = useState(false);
  const text = String(s.preview || '');
  const long = text.length > 220;
  return (
    <li id={`${domId}-src-${s.ref}`} className={highlight === s.ref ? 'hl' : ''}>
      <span className="cite static">{s.ref}</span>
      <div className="src-body">
        <span className="muted small">{s.page != null ? `page ${s.page}` : `part ${Number(s.chunk_index) + 1}`}</span>
        {text ? <p dir="auto">{full ? text : clamp(text, 220)}</p> : null}
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

function Doc({ group, domId, highlight }) {
  return (
    <li className="src-doc">
      <div className="src-doc-head">
        <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><path d="M4 1.8h5l3 3V14H4zM9 1.8V5h3" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" /></svg>
        <strong dir="auto">{group.doc_id}</strong>
        <span className="relevance" title="Best match with your question">
          <span className="bar"><i style={{ width: percent(group.best) }} /></span>
          {percent(group.best)}
        </span>
      </div>
      <ul className="src-passages">
        {group.items.map((s) => <Passage key={s.ref} s={s} domId={domId} highlight={highlight} />)}
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
            {cited.map((g) => <Doc key={g.doc_id} group={g} domId={domId} highlight={highlight} />)}
            {citedWeb.map((w) => <Web key={w.ref} s={w} domId={domId} highlight={highlight} />)}
          </ul>
          {(more.length > 0 || moreWeb.length > 0) && (
            <>
              <button type="button" className="link-btn more-btn" onClick={() => setShowMore((v) => !v)}>
                {showMore ? 'Hide' : 'Show'} {plan.more.length} other retrieved {plan.more.length === 1 ? 'passage' : 'passages'}
              </button>
              {showMore && (
                <ul className="sources-list faded">
                  {more.map((g) => <Doc key={g.doc_id} group={g} domId={domId} highlight={highlight} />)}
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
