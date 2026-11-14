// Chhota, safe markdown parser: text => AST (React elements mein render hota hai, innerHTML nahi).
// Raw HTML sirf text ban jata hai. Links sirf http/https/mailto.

const ESCAPABLE = '\\`*_{}[]()#+-.!~|>';
const FENCE = /^\s{0,3}(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/;
const HR = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;
const LIST = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

export function safeUrl(raw) {
  const url = String(raw || '').trim();
  if (!url || /[\u0000-\u001f\s]/.test(url)) return null;
  const m = /^([a-z][a-z0-9+.-]*):/i.exec(url);
  if (!m) return null;
  return ['http', 'https', 'mailto'].includes(m[1].toLowerCase()) ? url : null;
}

const isWordChar = (ch) => !!ch && /[\p{L}\p{N}]/u.test(ch);
const MARKS = [['**', 'strong'], ['__', 'strong'], ['~~', 'del'], ['*', 'em'], ['_', 'em']];

export function parseInline(src) {
  const nodes = [];
  let buf = '';
  let i = 0;
  const flush = () => { if (buf) { nodes.push({ type: 'text', text: buf }); buf = ''; } };

  outer: while (i < src.length) {
    const ch = src[i];

    if (ch === '\n') { flush(); nodes.push({ type: 'br' }); i++; continue; }

    if (ch === '\\' && i + 1 < src.length && ESCAPABLE.includes(src[i + 1])) {
      buf += src[i + 1]; i += 2; continue;
    }

    if (ch === '`') {
      let n = 1;
      while (src[i + n] === '`') n++;
      const ticks = '`'.repeat(n);
      const end = src.indexOf(ticks, i + n);
      if (end !== -1 && end > i + n) {
        flush();
        nodes.push({ type: 'code', text: src.slice(i + n, end).replace(/^ (.*) $/s, '$1') });
        i = end + n;
      } else { buf += ticks; i += n; }
      continue;
    }

    if (ch === '[') {
      const m = /^\[([^\]]+)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)/.exec(src.slice(i));
      if (m) {
        flush();
        nodes.push({ type: 'link', href: safeUrl(m[2]), children: parseInline(m[1]) });
        i += m[0].length;
        continue;
      }
    }

    if (ch === 'h' && !isWordChar(src[i - 1]) && (src.startsWith('http://', i) || src.startsWith('https://', i))) {
      const m = /^https?:\/\/[^\s<>]+/.exec(src.slice(i));
      let url = m[0];
      const trail = /[.,;:!?)\]'"]+$/.exec(url);
      if (trail) url = url.slice(0, -trail[0].length);
      const href = safeUrl(url);
      if (href) {
        flush();
        nodes.push({ type: 'link', href, children: [{ type: 'text', text: url }] });
        i += url.length;
        continue;
      }
    }

    for (const [mark, type] of MARKS) {
      if (!src.startsWith(mark, i)) continue;
      const open = i + mark.length;
      const next = src[open];
      const underscoreInWord = mark[0] === '_' && isWordChar(src[i - 1]);
      let found = -1;

      if (next && !/\s/.test(next) && !underscoreInWord) {
        let end = open;
        while ((end = src.indexOf(mark, end)) !== -1) {
          const before = src[end - 1];
          const after = src[end + mark.length];
          if (end > open && !/\s/.test(before) && !(mark[0] === '_' && isWordChar(after))) { found = end; break; }
          end += mark.length;
        }
      }

      if (found !== -1) {
        flush();
        nodes.push({ type, children: parseInline(src.slice(open, found)) });
        i = found + mark.length;
        continue outer;
      }
      if (mark.length === 2) { buf += mark; i += 2; continue outer; }   // unclosed ** (streaming)
      break;                                                             // single * / _ => literal
    }

    buf += ch;
    i++;
  }
  flush();
  return nodes;
}

const isBlockStart = (line) => FENCE.test(line) || HEADING.test(line) || HR.test(line) || QUOTE.test(line);

function isListStart(line, interrupting = false) {
  const m = LIST.exec(line);
  if (!m) return false;
  if (HR.test(line)) return false;
  return !(interrupting && /\d/.test(m[2]) && parseInt(m[2], 10) !== 1);   // "2025. year" paragraph ko na toote
}

const isTableStart = (lines, i) =>
  i + 1 < lines.length && lines[i].includes('|') && lines[i + 1].includes('|') &&
  lines[i + 1].includes('-') && TABLE_SEP.test(lines[i + 1]);

function splitRow(line) {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|') && !s.endsWith('\\|')) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, '|'));
}

function parseTable(lines, start) {
  const header = splitRow(lines[start]);
  const align = splitRow(lines[start + 1]).map((c) =>
    c.startsWith(':') && c.endsWith(':') ? 'center' : c.endsWith(':') ? 'right' : c.startsWith(':') ? 'left' : null);
  let i = start + 2;
  const rows = [];
  while (i < lines.length && lines[i].trim() !== '' && lines[i].includes('|')) {
    const cells = splitRow(lines[i]);
    while (cells.length < header.length) cells.push('');
    rows.push(cells.slice(0, header.length).map(parseInline));
    i++;
  }
  return [{ type: 'table', align, header: header.map(parseInline), rows }, i];
}

const indentOf = (line) => line.match(/^\s*/)[0].length;

function parseList(lines, start) {
  const first = LIST.exec(lines[start]);
  const baseIndent = first[1].length;
  const ordered = /\d/.test(first[2]);
  const startNum = ordered ? parseInt(first[2], 10) : null;
  const items = [];
  let i = start;

  while (i < lines.length) {
    const m = LIST.exec(lines[i]);
    if (!m || m[1].length !== baseIndent || /\d/.test(m[2]) !== ordered || HR.test(lines[i])) break;
    const itemLines = [m[3]];
    i++;

    while (i < lines.length) {
      const l = lines[i];
      if (l.trim() === '') {
        let j = i + 1;
        while (j < lines.length && lines[j].trim() === '') j++;
        if (j >= lines.length) { i = j; break; }
        const nm = LIST.exec(lines[j]);
        const deeper = indentOf(lines[j]) > baseIndent;
        const sibling = nm && nm[1].length === baseIndent && /\d/.test(nm[2]) === ordered;
        if (deeper || sibling) { itemLines.push(''); i++; continue; }
        break;
      }
      const indent = indentOf(l);
      if (indent > baseIndent) { itemLines.push(l.slice(Math.min(indent, baseIndent + 2))); i++; continue; }
      if (LIST.test(l) || isBlockStart(l)) break;
      itemLines.push(l);                // lazy continuation
      i++;
    }
    while (itemLines.length && itemLines[itemLines.length - 1] === '') itemLines.pop();
    items.push(parseBlocks(itemLines));
  }
  return [{ type: 'list', ordered, start: startNum, items }, i];
}

function parseBlocks(lines) {
  const out = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === '') { i++; continue; }
    let m;

    if ((m = FENCE.exec(line))) {
      const ch = m[1][0];
      const len = m[1].length;
      const closing = new RegExp(`^\\s{0,3}${ch}{${len},}\\s*$`);
      const body = [];
      i++;
      while (i < lines.length && !closing.test(lines[i])) { body.push(lines[i]); i++; }
      i++;                              // closing fence (stream beech mein ho to bhi theek)
      out.push({ type: 'code', lang: m[2] || '', text: body.join('\n') });
      continue;
    }

    if ((m = HEADING.exec(line))) {
      out.push({ type: 'heading', level: m[1].length, children: parseInline(m[2]) });
      i++;
      continue;
    }

    if (HR.test(line)) { out.push({ type: 'hr' }); i++; continue; }

    if (QUOTE.test(line)) {
      const inner = [];
      while (i < lines.length && QUOTE.test(lines[i])) { inner.push(QUOTE.exec(lines[i])[1]); i++; }
      out.push({ type: 'blockquote', children: parseBlocks(inner) });
      continue;
    }

    if (isListStart(line)) {
      const [node, next] = parseList(lines, i);
      out.push(node);
      i = next;
      continue;
    }

    if (isTableStart(lines, i)) {
      const [node, next] = parseTable(lines, i);
      out.push(node);
      i = next;
      continue;
    }

    const para = [line.trim()];
    i++;
    while (
      i < lines.length && lines[i].trim() !== '' && !isBlockStart(lines[i]) &&
      !isListStart(lines[i], true) && !isTableStart(lines, i)
    ) { para.push(lines[i].trim()); i++; }
    out.push({ type: 'paragraph', children: parseInline(para.join('\n')) });
  }
  return out;
}

export function parseMarkdown(src) {
  return parseBlocks(String(src ?? '').replace(/\r\n?/g, '\n').split('\n'));
}

// Test/debug ke liye: AST ka plain text
export function toPlainText(nodes) {
  return nodes.map((n) => {
    if (n.type === 'text' || n.type === 'code') return n.text;
    if (n.type === 'br') return '\n';
    if (n.children) return toPlainText(n.children);
    return '';
  }).join('');
}
