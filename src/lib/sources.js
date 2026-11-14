// Jawab ke sources ko saaf dikhane ke liye:
// - "I could not find..." wale jawab par sources chhupa do (retrieval ke 4 chunks bekar hain)
// - jo [n] jawab mein cite hue wo "cited" hain, baaqi "more" mein
// - same document ke passages ek card mein group
const NOT_FOUND = /could not find|couldn'?t find|not (?:found )?in the documents|i (?:do not|don'?t) know|no information|not (?:mentioned|available) in|contact the sales team|دستاویزات میں نہیں|نہیں مل|معلوم نہیں/i;

export function isNotFound(text) {
  return NOT_FOUND.test(String(text || '').slice(0, 160));
}

export function citedRefs(text) {
  const out = new Set();
  for (const m of String(text || '').matchAll(/\[(\d{1,2})\]/g)) out.add(Number(m[1]));
  return out;
}

export function groupByDoc(list) {
  const map = new Map();
  for (const s of list) {
    if (!map.has(s.doc_id)) map.set(s.doc_id, { doc_id: s.doc_id, items: [] });
    map.get(s.doc_id).items.push(s);
  }
  return [...map.values()].map((g) => ({
    ...g,
    best: Math.max(...g.items.map((i) => Number(i.score) || 0)),
  }));
}

export function planSources(sources, content, streaming = false) {
  // Server-side calculator/unit-converter ke "sources" panel mein nahi dikhte (origin label unhein sambhalta hai)
  const all = (Array.isArray(sources) ? sources : []).filter((s) => s.kind !== 'tool');
  if (!all.length) return { hidden: true, cited: [], more: [] };
  if (!streaming && isNotFound(content)) return { hidden: true, cited: [], more: [] };
  const refs = citedRefs(content);
  const cited = all.filter((s) => refs.has(s.ref));
  if (!cited.length) return { hidden: false, cited: all, more: [], uncited: true };
  return { hidden: false, cited, more: all.filter((s) => !refs.has(s.ref)) };
}

export const isWeb = (s) => !!s && (s.kind === 'web' || (!!s.url && s.kind !== 'doc'));

export const isTool = (s) => !!s && s.kind === 'tool';

// Jawab kahan se aaya: 'tool' | 'docs' | 'web' | 'mixed' | null
export function originOf(sources) {
  const list = Array.isArray(sources) ? sources : [];
  if (!list.length) return null;
  if (list.every(isTool)) return 'tool';
  const all = list.filter((s) => !isTool(s));
  const web = all.some(isWeb);
  const docs = all.some((s) => !isWeb(s));
  return web && docs ? 'mixed' : web ? 'web' : 'docs';
}

export function domainOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return ''; }
}
