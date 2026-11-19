import { useEffect, useMemo, useRef, useState } from 'react';
import { clamp, groupByRecency, initials } from '../lib/format.js';
import { avatarFor } from '../lib/avatars.js';
import { applyTheme, getTheme, saveTheme } from '../lib/theme.js';

const Icon = ({ d, size = 18 }) => (
  <svg viewBox="0 0 20 20" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={d} />
  </svg>
);
const ICONS = {
  plus: 'M10 4v12M4 10h12',
  search: 'M9 15.5a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM14 14l3.5 3.5',
  shield: 'M10 2.5 16 5v4.5c0 3.6-2.4 6.3-6 8-3.6-1.7-6-4.4-6-8V5z',
  calc: 'M5 2.5h10a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-13a1 1 0 0 1 1-1zM7 6h6M7 10h.01M10 10h.01M13 10h.01M7 13.5h.01M10 13.5h.01M13 13.5h.01',
  panel: 'M3 4.5A1.5 1.5 0 0 1 4.5 3h11A1.5 1.5 0 0 1 17 4.5v11a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 3 15.5zM8 3v14',
  logout: 'M8 3.5H5a1.5 1.5 0 0 0-1.5 1.5v10A1.5 1.5 0 0 0 5 16.5h3M12.5 6.5 16 10l-3.5 3.5M16 10H8',
};
const SUN = 'M10 14.2a4.2 4.2 0 1 0 0-8.4 4.2 4.2 0 0 0 0 8.4zM10 1.8v1.7M10 16.5v1.7M1.8 10h1.7M16.5 10h1.7M4.2 4.2l1.2 1.2M14.6 14.6l1.2 1.2M4.2 15.8l1.2-1.2M14.6 5.4l1.2-1.2';
const MOON = 'M16.5 11.6A6.8 6.8 0 0 1 8.4 3.5a6.8 6.8 0 1 0 8.1 8.1z';

function UserAvatar({ username }) {
  const a = avatarFor(username);
  if (a) return <img className="avatar photo" src={a.src} alt="" width="36" height="36" />;
  return <span className="avatar small" aria-hidden="true">{initials(username)}</span>;
}

// Day/night switch dabane par avatar apni jagah se nikalta hai, switch tak jata hai, khud dabata hai (theme tabhi badalti hai),
// phir wapis apni jagah baith jata hai. Animation band (prefers-reduced-motion) ho to seedha badal deta hai.
function flipWithAvatar(avatarEl, switchEl, commit, done) {
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  if (!avatarEl || !switchEl || reduce || !avatarEl.animate) { commit(); done(); return () => {}; }
  const a = avatarEl.getBoundingClientRect();
  const b = switchEl.getBoundingClientRect();
  if (!a.width || !b.width) { commit(); done(); return () => {}; }
  const ghost = avatarEl.cloneNode(true);
  Object.assign(ghost.style, {
    position: 'fixed', left: `${a.left}px`, top: `${a.top}px`, width: `${a.width}px`, height: `${a.height}px`,
    margin: '0', zIndex: '3000', pointerEvents: 'none', boxShadow: '0 8px 22px rgba(0,0,0,.28)',
  });
  document.body.appendChild(ghost);
  avatarEl.style.visibility = 'hidden';                    // avatar apni jagah se "nikal" gaya
  const dx = b.left + b.width / 2 - (a.left + a.width / 2);
  const dy = b.top + b.height / 2 - (a.top + a.height / 2);
  const stop = [];
  const finish = () => { ghost.remove(); avatarEl.style.visibility = ''; done(); };
  const out = ghost.animate([
    { transform: 'translate(0,0) scale(1) rotate(0deg)' },
    { transform: `translate(${dx * 0.45}px, ${dy * 0.45 - 34}px) scale(1.22) rotate(-10deg)`, offset: 0.5 },
    { transform: `translate(${dx}px, ${dy - 4}px) scale(0.78) rotate(0deg)` },
  ], { duration: 520, easing: 'cubic-bezier(.3,.7,.3,1)', fill: 'forwards' });
  stop.push(() => out.cancel());
  out.onfinish = () => {
    switchEl.animate([{ transform: 'scale(1)' }, { transform: 'scale(0.78)' }, { transform: 'scale(1)' }], { duration: 240, easing: 'ease-out' });
    commit();                                              // avatar ne switch daba diya
    const back = ghost.animate([
      { transform: `translate(${dx}px, ${dy - 4}px) scale(0.78)` },
      { transform: `translate(${dx * 0.5}px, ${dy * 0.5 - 24}px) scale(1.12) rotate(8deg)`, offset: 0.5 },
      { transform: 'translate(0,0) scale(1) rotate(0deg)' },
    ], { duration: 480, delay: 160, easing: 'cubic-bezier(.3,.7,.3,1)', fill: 'forwards' });
    stop.push(() => back.cancel());
    back.onfinish = finish;
  };
  return () => { stop.forEach((fn) => fn()); finish(); };
}

function Item({ conv, active, onSelect, onRename, onDelete }) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(conv.title);
  const input = useRef(null);

  useEffect(() => { if (editing) { input.current?.focus(); input.current?.select(); } }, [editing]);

  async function commit() {
    const next = title.trim();
    setEditing(false);
    if (next && next !== conv.title) await onRename(conv.id, next);
    else setTitle(conv.title);
  }

  if (editing) {
    return (
      <li className="conv editing">
        <input
          ref={input}
          dir="auto"
          value={title}
          maxLength={200}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit();
            if (e.key === 'Escape') { setTitle(conv.title); setEditing(false); }
          }}
          aria-label="Conversation title"
        />
      </li>
    );
  }

  return (
    <li className={`conv ${active ? 'active' : ''}`}>
      <button type="button" className="conv-title" onClick={() => onSelect(conv.id)} dir="auto" title={conv.title}>
        {clamp(conv.title, 60)}
      </button>
      <span className="conv-actions">
        <button type="button" className="icon-btn" onClick={() => { setTitle(conv.title); setEditing(true); }} aria-label={`Rename ${conv.title}`} title="Rename">
          <svg viewBox="0 0 16 16" width="14" height="14"><path d="M11 2.5 13.5 5 5.5 13H3v-2.5z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></svg>
        </button>
        <button type="button" className="icon-btn" onClick={() => onDelete(conv)} aria-label={`Delete ${conv.title}`} title="Delete">
          <svg viewBox="0 0 16 16" width="14" height="14"><path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8h5.8l.6-8" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
      </span>
    </li>
  );
}

export default function Sidebar({
  conversations, activeId, loaded, onSelect, onNew, onRename, onDelete,
  user, view, onView, onPassword, onLogout, open, onClose, onCollapse,
}) {
  const [menu, setMenu] = useState(false);
  const [query, setQuery] = useState('');
  const [theme, setTheme] = useState(getTheme);

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q ? conversations.filter((c) => c.title.toLowerCase().includes(q)) : conversations;
    return groupByRecency(list);
  }, [conversations, query]);
  const shownCount = groups.reduce((n, g) => n + g.items.length, 0);

  useEffect(() => {
    if (!menu) return undefined;
    const close = (e) => { if (!e.target.closest('.user-menu, .user-btn')) setMenu(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [menu]);

  const avatarRef = useRef(null);
  const switchRef = useRef(null);
  const flipping = useRef(false);
  const cancelFlip = useRef(() => {});
  useEffect(() => () => cancelFlip.current(), []);
  const toggleTheme = () => {
    if (flipping.current) return;                           // animation chal rahi hai: dobara click ignore
    const next = theme === 'dark' ? 'light' : 'dark';
    flipping.current = true;
    cancelFlip.current = flipWithAvatar(
      avatarRef.current?.firstElementChild, switchRef.current,
      () => { applyTheme(next); saveTheme(next); setTheme(next); },
      () => { flipping.current = false; },
    );
  };

  return (
    <>
      {open && <div className="scrim" onClick={onClose} />}
      <aside className={`sidebar ${open ? 'open' : ''}`} aria-label="Sidebar">
        <div className="brand">
          <img src="/logo.jpg" alt="" width="30" height="30" />
          <span className="wordmark">MetaXpert</span>
          <button type="button" className="icon-btn collapse-btn" onClick={onCollapse} aria-label="Hide sidebar" title="Hide sidebar">
            <Icon d={ICONS.panel} />
          </button>
        </div>

        <div className="side-pad">
          <button type="button" className="new-chat" onClick={onNew}>
            <Icon d={ICONS.plus} /> <span>New chat</span>
          </button>
        </div>

        <div className="side-search">
          <Icon d={ICONS.search} size={16} />
          <input
            dir="auto"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Escape') setQuery(''); }}
            placeholder="Search chats"
            aria-label="Search chats"
          />
        </div>

        <nav className="nav" aria-label="Main">
          <button type="button" className={`nav-item ${view === 'calc' ? 'on' : ''}`} onClick={() => onView(view === 'calc' ? 'chat' : 'calc')}>
            <Icon d={ICONS.calc} /> <span>Calculator</span>
          </button>
          {(user.role === 'admin' || user.role === 'company_admin') && (
            <button type="button" className={`nav-item ${view === 'admin' ? 'on' : ''}`} onClick={() => onView(view === 'admin' ? 'chat' : 'admin')}>
              <Icon d={ICONS.shield} /> <span>{user.role === 'admin' ? 'Admin' : 'My company'}</span>
            </button>
          )}
        </nav>

        <div className="conv-list">
          {!loaded && <p className="muted pad">Loading…</p>}
          {loaded && !conversations.length && <p className="muted pad">No chats yet. Ask your first question.</p>}
          {loaded && conversations.length > 0 && !shownCount && <p className="muted pad">No chats match “{query}”.</p>}
          {groups.map((g) => (
            <section key={g.label}>
              <h3 className="group-title">{g.label}</h3>
              <ul>
                {g.items.map((c) => (
                  <Item key={c.id} conv={c} active={view === 'chat' && c.id === activeId}
                    onSelect={onSelect} onRename={onRename} onDelete={onDelete} />
                ))}
              </ul>
            </section>
          ))}
        </div>

        <div className="side-bottom">
          {menu && (
            <div className="user-menu" role="menu">
              <button type="button" role="menuitem" onClick={() => { setMenu(false); onPassword(); }}>Change password</button>
              <button type="button" role="menuitem" onClick={() => { setMenu(false); onLogout(); }}>Sign out</button>
            </div>
          )}
          <div className="user-card">
            <button type="button" className="user-btn" onClick={() => setMenu((m) => !m)} aria-haspopup="menu" aria-expanded={menu}>
              <span className="avatar-slot" ref={avatarRef}><UserAvatar username={user.username} /></span>
              <span className="user-meta">
                <span className="user-name">{user.username}</span>
                <span className="user-role">{user.role === 'company_admin' ? 'company admin' : user.role}{user.company ? ` · ${user.company}` : ''}</span>
              </span>
            </button>
            <button
              type="button"
              className="theme-btn"
              ref={switchRef}
              onClick={toggleTheme}
              aria-label={theme === 'dark' ? 'Switch to day mode' : 'Switch to night mode'}
              title={theme === 'dark' ? 'Day mode' : 'Night mode'}
            >
              <Icon d={theme === 'dark' ? SUN : MOON} />
            </button>
            <button type="button" className="logout-btn" onClick={onLogout} aria-label="Sign out" title="Sign out">
              <Icon d={ICONS.logout} />
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}
