// App-wide singletons: token store + api client. 401 par token hatao aur sunne walon ko batao.
import { createApi } from './api.js';
import { createTokenStore } from './storage.js';

function safeStorage(kind) {
  try { return globalThis[kind]; } catch { return null; }
}
const memory = () => {
  const m = new Map();
  return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) };
};

export const tokenStore = createTokenStore(
  safeStorage('localStorage') || memory(),
  safeStorage('sessionStorage') || memory(),
);

const unauthorizedListeners = new Set();
export function onUnauthorized(fn) {
  unauthorizedListeners.add(fn);
  return () => unauthorizedListeners.delete(fn);
}

export const api = createApi({
  baseUrl: '/api/v1',
  getToken: () => tokenStore.get(),
  onUnauthorized: (err) => {
    tokenStore.clear();
    unauthorizedListeners.forEach((fn) => fn(err));
  },
});

// Insaan ke samajhne wala error message (API.md ke rules)
export function describeError(err, fallback = 'Something went wrong') {
  if (!err) return fallback;
  if (err.name === 'ApiError') {
    switch (err.status) {
      case 0: return err.detail;
      case 403: return err.detail && err.detail !== 'Forbidden' ? err.detail : 'You do not have access to this.';
      case 429:
        if (/queue/i.test(err.detail || '')) return 'Server is busy right now, please try again in a few seconds.';
        if (/in progress/i.test(err.detail || '')) return 'Your previous question is still being answered. Please wait for it to finish.';
        return err.retryAfter
          ? `Too many requests. Try again in ${err.retryAfter}s.`
          : 'Too many requests. Try again shortly.';
      case 502: case 504: return 'The model is busy or down. Try again in a moment.';
      default: return err.detail || fallback;
    }
  }
  return err.message || fallback;
}
