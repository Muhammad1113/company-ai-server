// Jawab ki "maturity": jawab ka har hissa kahan se aaya, wo jawab ke apne text se tay hota hai
// (sirf is se nahi ke retrieval ne sources lautaye). Koi extra GPU call nahi, sab rules se.
import { citedRefs, isNotFound, isTool, isWeb } from './sources.js';

// "General knowledge:" / "From general knowledge:" label (model jawab ke hisse ke shuru mein likhta hai)
export const GK_PREFIX = /^\s*(?:from\s+)?general knowledge\s*[:\-–—]\s*/i;

// Model kabhi kabhi apni taraf se "Note: This answer is based on general knowledge ..." jod deta hai.
// Wo UI ka kaam hai (badge), jawab ka hissa nahi: display se hata dete hain.
const META_NOTE = /^\s*(?:\*\*|_)?(?:note|disclaimer)(?:\*\*|_)?\s*[:\-–—]\s*.*?(?:general knowledge|provided (?:context|documents?)|context (?:only|provided)|company documents?|based on (?:the )?(?:context|documents?))/i;
const OLD_BANNER = /^\s*(?:check numbers and certificates against the source.*|no source\. this answer comes from.*)$/i;

// Model kabhi jawab dene ke baad bhi "This is not in the company documents." jod deta hai (khud ka contradiction)
const NOT_IN_DOCS_LINE = /^\s*this is not in the company documents\.?\s*$/i;

export function cleanAnswer(text) {
  const lines = String(text || '').split('\n').filter((l) => !META_NOTE.test(l) && !OLD_BANNER.test(l));
  const rest = lines.filter((l) => !NOT_IN_DOCS_LINE.test(l));
  // Sirf tab hatao jab uske ilawa asal jawab maujood ho; akeli line wala jawab (sach mein "nahi mila") rehta hai
  const kept = rest.join('\n').trim().length >= 40 ? rest : lines;
  return kept.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

// "General knowledge:" label (purane jawabon mein, shuru ya jumle ke beech "Wa alaikum assalam. General knowledge: ...")
// ab UI mein kabhi nahi dikhta: jawab ek hi awaaz mein hona chahiye, origin badge alag se bata deta hai.
const GK_INLINE = /(^|[.!?]\s+|\n)\s*(?:from\s+)?general knowledge\s*[:\-–—]\s*/gi;

export function dropGeneralLabel(text) {
  return String(text || '').replace(GK_INLINE, '$1').replace(/^\s+/, '').trim();
}

const hasCite = (line, refs) => {
  for (const m of line.matchAll(/\[(\d{1,2})\]/g)) if (!refs || refs.has(Number(m[1]))) return true;
  return false;
};

// Jawab ke "bayaan": paragraph / bullet / numbered line (heading aur chhoti lines nahi)
export function claimLines(text) {
  return String(text || '').split('\n')
    .map((l) => l.replace(/^\s*(?:[-*+•]|\d{1,2}[.)])\s+/, '').trim())
    .filter((l) => l.length >= 40 && !/^#{1,6}\s/.test(l));
}

/**
 * 'tool' | 'docs' | 'docgen' | 'general' | 'web' | 'mixed' | null
 *  docs    = jawab poora documents se (har bayaan par [n])
 *  docgen  = kuch hissa documents se ([n]), baaki general knowledge (bina [n])
 *  general = documents mile/dekhe gaye magar jawab mein koi use nahi hua
 *  web     = internet; mixed = documents + internet
 *  null    = koi label nahi (salam, "I could not find", seedhi baat-cheet)
 */
export function answerOrigin(content, sources) {
  const text = String(content || '');
  if (!text || isNotFound(text)) return null;
  const list = (Array.isArray(sources) ? sources : []);
  const explicitGeneral = GK_PREFIX.test(text) || /\n\s*(?:from\s+)?general knowledge\s*[:\-–—]/i.test(text);

  if (!list.length) return explicitGeneral ? 'general' : null;
  if (list.every(isTool)) return 'tool';

  const real = list.filter((s) => !isTool(s));
  const web = real.filter(isWeb);
  const docs = real.filter((s) => !isWeb(s));
  const refs = citedRefs(text);

  if (web.length && !docs.length) return 'web';
  if (web.length && docs.length) return 'mixed';

  const citedDocs = docs.filter((s) => refs.has(s.ref));
  if (!citedDocs.length) return 'general';           // sources mile the, jawab ne use nahi kiye: unhe "document answer" mat banao

  const validRefs = new Set(docs.map((s) => s.ref));
  const uncited = claimLines(text).filter((l) => !hasCite(l, validRefs) && !GK_PREFIX.test(l));
  return explicitGeneral || uncited.length ? 'docgen' : 'docs';
}

// ------------------------------------------------------------ passage highlight
const STOP = new Set(['the', 'and', 'for', 'are', 'was', 'were', 'with', 'that', 'this', 'from', 'have', 'has', 'not', 'any',
  'all', 'can', 'may', 'must', 'shall', 'will', 'its', 'their', 'than', 'then', 'into', 'such', 'each', 'per', 'under', 'over',
  'which', 'who', 'when', 'where', 'what', 'how', 'also', 'but', 'you', 'your', 'our', 'ko', 'ka', 'ki', 'ke', 'hai', 'hain',
  'mein', 'par', 'se', 'aur']);

const words = (s) => (String(s || '').toLowerCase().match(/[\p{L}\p{N}][\p{L}\p{N}./%-]*/gu) || [])
  .map((w) => w.replace(/[.\-/]+$/, ''))
  .filter((w) => (w.length >= 3 || /\d/.test(w)) && !STOP.has(w));

// Jawab ke woh bayaan jin par [ref] laga hai
export function citingText(content, ref) {
  const tag = `[${ref}]`;
  return String(content || '').split('\n').filter((l) => l.includes(tag)).join(' ').replace(/\[\d{1,2}\]/g, ' ');
}

/**
 * Passage ke andar wo jumle jo jawab ke bayaan se sab se zyada milte hain => [[start, end], ...] (char offsets).
 * Bayaan ke alfaaz (khaas kar numbers/codes) passage ke jumle se milaye jate hain: max 2 jumle.
 */
export function highlightRanges(passage, claim) {
  const text = String(passage || '');
  const claimWords = new Set(words(claim));
  if (!text || claimWords.size < 2) return [];
  const spans = [];
  const re = /[^.!?;\n]+(?:[.!?;]+|\n|$)/g;
  let m;
  while ((m = re.exec(text))) {
    if (m[0].trim().length >= 12) spans.push([m.index, m.index + m[0].length, m[0]]);
    if (m[0].length === 0) re.lastIndex++;
  }
  const scored = spans.map(([s, e, t]) => {
    const w = new Set(words(t));
    let hit = 0;
    for (const x of w) if (claimWords.has(x)) hit += /\d/.test(x) ? 2 : 1;
    return { s, e, score: hit / Math.sqrt(Math.max(w.size, 4)), hit };
  }).filter((x) => x.hit >= 2);
  if (!scored.length) return [];
  const best = Math.max(...scored.map((x) => x.score));
  return scored.filter((x) => x.score >= best * 0.6).sort((a, b) => b.score - a.score).slice(0, 2)
    .sort((a, b) => a.s - b.s).map((x) => {
      const chunk = text.slice(x.s, x.e);
      const lead = chunk.length - chunk.trimStart().length;
      return [x.s + lead, x.s + lead + chunk.trim().length];
    });
}

// Chhota excerpt: pehle highlight ke aas paas ka hissa (taake highlight collapsed view mein bhi dikhe)
export function excerptWindow(text, ranges, max = 260) {
  const t = String(text || '');
  if (t.length <= max) return [0, t.length];
  if (!ranges.length) return [0, max];
  const [rs, re] = ranges[0];
  const start = Math.max(0, Math.min(rs - 60, t.length - max));
  return [start, Math.min(t.length, Math.max(start + max, Math.min(re + 60, start + max + 120)))];
}
