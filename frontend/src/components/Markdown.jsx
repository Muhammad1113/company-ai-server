import { Fragment, memo, useMemo, useState } from 'react';
import { parseMarkdown } from '../lib/markdown.js';
import { copyText } from '../lib/clipboard.js';
import { GK_PREFIX } from '../lib/answer.js';

// AST => React elements (innerHTML kabhi nahi). [n] sources ho to citation chip bante hain.

function CodeBlock({ lang, text }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    if (await copyText(text)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };
  return (
    <div className="codeblock" dir="ltr">
      <div className="codeblock-bar">
        <span>{lang || 'text'}</span>
        <button type="button" className="link-btn" onClick={onCopy}>{copied ? 'Copied' : 'Copy'}</button>
      </div>
      <pre><code>{text}</code></pre>
    </div>
  );
}

const CITE = /(\[\d{1,2}\])/g;

function renderText(text, key, ctx) {
  if (!ctx.refs.size) return text;
  const parts = text.split(CITE);
  if (parts.length === 1) return text;
  return parts.map((part, i) => {
    const m = /^\[(\d{1,2})\]$/.exec(part);
    const n = m ? Number(m[1]) : null;
    if (n !== null && ctx.refs.has(n)) {
      return (
        <button
          key={`${key}-${i}`}
          type="button"
          className="cite"
          onClick={() => ctx.onCite?.(n)}
          aria-label={`Show source ${n}`}
          title={ctx.titles?.get(n) || `Source ${n}`}
        >{n}</button>
      );
    }
    return <Fragment key={`${key}-${i}`}>{part}</Fragment>;
  });
}

function plain(nodes) {
  return (nodes || []).map((n) => (n.type === 'text' || n.type === 'code' ? n.text : n.children ? plain(n.children) : '')).join('');
}

// Ek paragraph/bullet ka darja (sirf "documents + general" jawab mein): 'doc' = [n] wala, 'gen' = general knowledge
function segOf(text, ctx) {
  if (!ctx.mark) return null;
  if (GK_PREFIX.test(text)) return 'gen';
  for (const m of text.matchAll(/\[(\d{1,2})\]/g)) if (ctx.refs.has(Number(m[1]))) return 'doc';
  return text.trim().length >= 40 ? 'gen' : null;
}

// "General knowledge:" label hata do (badge/tag wo kaam karta hai); text node ki shuruaat se
function stripLabel(nodes, ctx) {
  if (!ctx.mark || !nodes.length || nodes[0].type !== 'text' || !GK_PREFIX.test(nodes[0].text)) return nodes;
  return [{ ...nodes[0], text: nodes[0].text.replace(GK_PREFIX, '') }, ...nodes.slice(1)];
}

function Inline({ nodes, ctx }) {
  return nodes.map((n, i) => {
    switch (n.type) {
      case 'text': return <Fragment key={i}>{renderText(n.text, i, ctx)}</Fragment>;
      case 'br': return <br key={i} />;
      case 'code': return <code key={i} className="inline-code">{n.text}</code>;
      case 'strong': return <strong key={i}><Inline nodes={n.children} ctx={ctx} /></strong>;
      case 'em': return <em key={i}><Inline nodes={n.children} ctx={ctx} /></em>;
      case 'del': return <del key={i}><Inline nodes={n.children} ctx={ctx} /></del>;
      case 'link':
        return n.href
          ? <a key={i} href={n.href} target="_blank" rel="noopener noreferrer nofollow"><Inline nodes={n.children} ctx={ctx} /></a>
          : <span key={i}><Inline nodes={n.children} ctx={ctx} /></span>;
      default: return null;
    }
  });
}

function Blocks({ nodes, ctx }) {
  return nodes.map((n, i) => {
    switch (n.type) {
      case 'paragraph': {
        const seg = segOf(plain(n.children), ctx);
        return (
          <p key={i} dir="auto" className={seg ? `ans-seg ans-${seg}` : undefined}>
            <Inline nodes={seg === 'gen' ? stripLabel(n.children, ctx) : n.children} ctx={ctx} />
          </p>
        );
      }
      case 'heading': {
        const Tag = `h${n.level}`;
        return <Tag key={i} dir="auto"><Inline nodes={n.children} ctx={ctx} /></Tag>;
      }
      case 'hr': return <hr key={i} />;
      case 'code': return <CodeBlock key={i} lang={n.lang} text={n.text} />;
      case 'blockquote': return <blockquote key={i} dir="auto"><Blocks nodes={n.children} ctx={ctx} /></blockquote>;
      case 'list': {
        const Tag = n.ordered ? 'ol' : 'ul';
        const props = n.ordered && n.start !== 1 && n.start != null ? { start: n.start } : {};
        return (
          <Tag key={i} dir="auto" {...props}>
            {n.items.map((item, j) => {
              const seg = segOf(item.map((b) => plain(b.children)).join(' '), ctx);
              return <li key={j} className={seg ? `ans-seg ans-${seg}` : undefined}><Blocks nodes={item} ctx={{ ...ctx, mark: false }} /></li>;
            })}
          </Tag>
        );
      }
      case 'table':
        return (
          <div key={i} className="table-wrap">
            <table dir="auto">
              <thead>
                <tr>{n.header.map((c, j) => (
                  <th key={j} style={n.align[j] ? { textAlign: n.align[j] } : undefined}><Inline nodes={c} ctx={ctx} /></th>
                ))}</tr>
              </thead>
              <tbody>
                {n.rows.map((row, r) => (
                  <tr key={r}>{row.map((c, j) => (
                    <td key={j} style={n.align[j] ? { textAlign: n.align[j] } : undefined}><Inline nodes={c} ctx={ctx} /></td>
                  ))}</tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      default: return null;
    }
  });
}

function Markdown({ text, sources, onCite, mark = false }) {
  const ast = useMemo(() => parseMarkdown(text), [text]);
  const refs = useMemo(() => new Set((sources || []).map((s) => s.ref)), [sources]);
  const titles = useMemo(() => new Map((sources || []).map((s) => [
    s.ref, s.page != null ? `${s.doc_id} · page ${s.page}` : String(s.title || s.doc_id || `Source ${s.ref}`)])), [sources]);
  const ctx = useMemo(() => ({ refs, titles, onCite, mark }), [refs, titles, onCite, mark]);
  return <div className="md"><Blocks nodes={ast} ctx={ctx} /></div>;
}

export default memo(Markdown);
