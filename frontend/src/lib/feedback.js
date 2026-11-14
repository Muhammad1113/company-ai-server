// Thumbs up/down: sirf is browser mein (localStorage). Server ko kuch nahi jata.
const KEY = 'cai.feedback';
const read = () => { try { return JSON.parse(localStorage.getItem(KEY) || '{}'); } catch { return {}; } };
export const getFeedback = (id) => (id ? read()[id] || 0 : 0);
export function setFeedback(id, v) {
  if (!id) return;
  try { const m = read(); if (v) m[id] = v; else delete m[id]; localStorage.setItem(KEY, JSON.stringify(m)); } catch { /* ignore */ }
}
