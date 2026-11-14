import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { api, onUnauthorized, tokenStore } from '../lib/services.js';
import { useToast } from './Toast.jsx';

const AuthContext = createContext(null);
export const useAuth = () => useContext(AuthContext);

const MAX_TIMER = 2 ** 31 - 1;

export function AuthProvider({ children }) {
  const { push } = useToast();
  const [session, setSession] = useState(() => tokenStore.session());
  const user = session?.user ?? null;

  // 401 kahin se bhi aaye => login page
  useEffect(() => onUnauthorized(() => {
    setSession(null);
    push('Your session ended. Sign in again.', 'error');
  }), [push]);

  // Token expire hone par khud logout
  useEffect(() => {
    if (!session?.expires_at) return undefined;
    const ms = session.expires_at * 1000 - Date.now();
    const t = setTimeout(() => {
      tokenStore.clear();
      setSession(null);
      push('Your session expired. Sign in again.', 'error');
    }, Math.min(Math.max(ms, 0), MAX_TIMER));
    return () => clearTimeout(t);
  }, [session?.expires_at, push]);

  const login = useCallback(async (username, password, remember) => {
    const res = await api.login(username.trim(), password);
    tokenStore.set(res.token, res.expires_at, res.user, remember);
    setSession(tokenStore.session());
  }, []);

  const logout = useCallback(async () => {
    await api.logout();            // fail ho to bhi local logout
    tokenStore.clear();
    setSession(null);
  }, []);

  const changePassword = useCallback(async (current, next) => {
    const res = await api.changePassword(current, next);
    tokenStore.refresh(res.token, res.expires_at);   // purane sab session band, naya token save
    setSession(tokenStore.session());
  }, []);

  const value = useMemo(() => ({ user, login, logout, changePassword }), [user, login, logout, changePassword]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
