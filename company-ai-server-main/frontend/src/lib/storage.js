// Token store: "remember me" => localStorage, warna sessionStorage (tab band => logout).
const KEY = 'cai.auth';

export function createTokenStore(local, session, now = () => Date.now()) {
  function locate() {
    for (const store of [session, local]) {
      try {
        const raw = store.getItem(KEY);
        if (!raw) continue;
        const value = JSON.parse(raw);
        if (value && value.token && (!value.expires_at || value.expires_at * 1000 > now())) {
          return { store, value };
        }
        store.removeItem(KEY);
      } catch { /* corrupt entry */ }
    }
    return null;
  }

  return {
    session: () => locate()?.value ?? null,
    get: () => locate()?.value.token ?? null,
    set(token, expires_at, user, remember) {
      this.clear();
      (remember ? local : session).setItem(KEY, JSON.stringify({ token, expires_at, user }));
    },
    // change-password ke baad naya token: usi storage mein
    refresh(token, expires_at) {
      const found = locate();
      if (found) found.store.setItem(KEY, JSON.stringify({ ...found.value, token, expires_at }));
    },
    updateUser(user) {
      const found = locate();
      if (found) found.store.setItem(KEY, JSON.stringify({ ...found.value, user }));
    },
    clear() {
      for (const store of [session, local]) {
        try { store.removeItem(KEY); } catch { /* ignore */ }
      }
    },
  };
}
