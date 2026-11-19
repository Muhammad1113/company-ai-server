import { useEffect, useState } from 'react';
import { useAuth } from '../context/Auth.jsx';
import { describeError } from '../lib/services.js';
import SpaceScene from './SpaceScene.jsx';

function Eye({ off }) {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M1.8 10S5 4.5 10 4.5 18.2 10 18.2 10 15 15.5 10 15.5 1.8 10 1.8 10z" />
      <circle cx="10" cy="10" r="2.5" />
      {off && <path d="M3 3l14 14" />}
    </svg>
  );
}

// .env mein VITE_LOGIN_MOTION=0 likhne se background bilkul ruk jata hai (purane PC ke liye)
const MOTION = import.meta.env?.VITE_LOGIN_MOTION !== '0';

export default function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [show, setShow] = useState(false);
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState('');
  const [wait, setWait] = useState(0);
  const [busy, setBusy] = useState(false);

  // 429 par countdown
  useEffect(() => {
    if (wait <= 0) return undefined;
    const t = setTimeout(() => setWait((w) => w - 1), 1000);
    return () => clearTimeout(t);
  }, [wait]);

  async function submit(e) {
    e.preventDefault();
    if (busy || wait > 0) return;
    setBusy(true);
    setError('');
    try {
      await login(username, password, remember);
    } catch (err) {
      setError(err?.status === 401 ? 'Wrong username or password.' : describeError(err, 'Could not sign in'));
      if (err?.status === 429) setWait(err.retryAfter || 30);
      setPassword('');
      setBusy(false);
    }
  }

  return (
    <main className={`auth ${MOTION ? '' : 'still'}`}>
      <div className="sky" aria-hidden="true">
        <div className="sky-bg" />
        <div className="stars stars-a" />
        <div className="stars stars-b" />
        <div className="galaxy-wrap"><img className="galaxy" src="/galaxy.webp" alt="" /></div>
      </div>
      <div className="auth-shell">
       <div className="auth-frame">
        <SpaceScene animate={MOTION} />
        <section className="auth-panel" aria-label="Sign in">
          <header className="auth-top">
            <img src="/logo.jpg" alt="" width="34" height="34" />
            <span>MetaXpert</span>
          </header>

          <div className="auth-body">
            <h1>
              <span className="thin">Tech</span>
              <strong>Beyond Stars</strong>
            </h1>
            <p className="auth-lead">Step into the world of digital assets and decentralized systems.</p>
            <p className="auth-tag">Driving innovation at the heart of decentralized ecosystems.</p>

            <div className="auth-divider"><span>sign in to MetaXpert AI</span></div>

            <form onSubmit={submit} className="auth-form">
              <label>Username
                <input
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="username"
                  autoCapitalize="none"
                  spellCheck={false}
                  placeholder="your username"
                  autoFocus
                  required
                />
              </label>

              <label>Password
                <span className="pw">
                  <input
                    type={show ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    autoComplete="current-password"
                    placeholder="your password"
                    required
                  />
                  <button type="button" className="pw-eye" onClick={() => setShow((s) => !s)} aria-label={show ? 'Hide password' : 'Show password'} aria-pressed={show}>
                    <Eye off={show} />
                  </button>
                </span>
              </label>

              <label className="auth-check">
                <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
                Remember me on this device
              </label>

              {error && <p className="auth-error" role="alert">{error}{wait > 0 ? ` Retry in ${wait}s.` : ''}</p>}

              <button type="submit" className="auth-btn" disabled={busy || wait > 0}>
                {busy ? 'Signing in…' : wait > 0 ? `Wait ${wait}s` : 'Login'}
              </button>
            </form>

            <p className="auth-help">Forgot your password or need an account? Contact your administrator.</p>
            {!window.isSecureContext && location.hostname !== 'localhost' && (
              <p className="auth-warn">This connection is not HTTPS. Your password can be read on the network.</p>
            )}
          </div>

          <footer className="auth-foot">© {new Date().getFullYear()} MetaXpert. All rights reserved.</footer>
        </section>
       </div>
      </div>
    </main>
  );
}
