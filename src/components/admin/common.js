export const NAME = /^[\w.\-:]+$/;
export const SLUG = /^[a-z0-9]+(?:_[a-z0-9]+)*$/;
export const SUFFIX = /^[a-z0-9][a-z0-9_.\-]*$/;      // collection ka naam (prefix ke baad); '__' allowed nahi
export const MAX_UPLOAD_MB = 20;
export const ALLOWED = ['.txt', '.md', '.pdf', '.docx'];

export const parseCollections = (text) => [...new Set(text.split(/[\s,]+/).filter(Boolean))];
export const validSuffix = (s) => SUFFIX.test(s) && !s.includes('__') && s.length <= 60;

// Collection ka "space": shared regulatory, kisi company ki, ya legacy
export function spaceOf(name) {
  if (name.startsWith('reg__')) return 'reg';
  const m = /^co_(.+?)__/.exec(name);
  return m ? m[1] : 'legacy';
}
export const prefixOf = (space) => (space === 'reg' ? 'reg__' : space === 'legacy' ? '' : `co_${space}__`);
export const suffixOf = (name) => name.slice(prefixOf(spaceOf(name)).length);

export const fmtMs = (ms) => (ms == null ? '–' : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`);
export const fmtIdle = (s) => (s < 60 ? 'now' : s < 3600 ? `${Math.floor(s / 60)} min ago` : `${Math.floor(s / 3600)} h ago`);
export const REGULATORY_DEFAULTS = ['fda', 'eu', 'uk', 'au'];
