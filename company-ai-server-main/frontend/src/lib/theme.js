// Day / night: user ki pasand localStorage mein; pehli dafa OS ki setting.
const KEY = 'cai.theme';

export function getTheme() {
  try {
    const t = localStorage.getItem(KEY);
    if (t === 'light' || t === 'dark') return t;
  } catch { /* storage band ho sakti hai */ }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
}

export function saveTheme(theme) {
  try { localStorage.setItem(KEY, theme); } catch { /* ignore */ }
}
