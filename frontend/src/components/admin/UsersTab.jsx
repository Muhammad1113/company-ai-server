import { useCallback, useEffect, useState } from 'react';
import { api, describeError } from '../../lib/services.js';
import { useToast } from '../../context/Toast.jsx';
import { NAME, parseCollections } from './common.js';

// mode 'super': super admin (sab companies).  mode 'company': company admin (sirf apni company).
const ROLE_LABEL = { user: 'User', company_admin: 'Company admin', admin: 'Super admin' };

function CreateUser({ mode, companies, defaultCompany, onCreated }) {
  const { push } = useToast();
  const [form, setForm] = useState({ username: '', password: '', role: 'user', company: defaultCompany || '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const needsCompany = mode === 'super' && form.role !== 'admin';

  useEffect(() => { if (defaultCompany) setForm((f) => ({ ...f, company: defaultCompany })); }, [defaultCompany]);

  async function submit(e) {
    e.preventDefault();
    if (needsCompany && !form.company) return setError('Choose a company for this user.');
    setBusy(true); setError('');
    try {
      const username = form.username.trim();
      if (mode === 'super') {
        await api.adminCreateUser({
          username, password: form.password, role: form.role,
          company: form.role === 'admin' ? null : form.company,
        });
      } else {
        await api.companyCreateUser({ username, password: form.password, role: 'user' });
      }
      push(`User “${username}” created.`, 'success');
      setForm({ ...form, username: '', password: '' });
      onCreated();
    } catch (err) {
      setError(describeError(err, 'Could not create the user'));
    } finally { setBusy(false); }
  }

  return (
    <form className="panel form" onSubmit={submit}>
      <h3>New user</h3>
      <div className="grid2">
        <label>Username
          <input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })}
            pattern="[A-Za-z0-9_.@\-]{3,64}" title="3 to 64 characters: letters, numbers, _ . @ -" autoCapitalize="none" required />
        </label>
        <label>Temporary password
          <input type="text" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })}
            minLength={8} maxLength={128} autoComplete="off" required />
        </label>
        {mode === 'super' && (
          <>
            <label>Role
              <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
                <option value="user">User</option>
                <option value="company_admin">Company admin</option>
                <option value="admin">Super admin (no company)</option>
              </select>
            </label>
            {needsCompany && (
              <label>Company
                <select value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} required>
                  <option value="">Choose…</option>
                  {companies.map((c) => <option key={c.slug} value={c.slug} disabled={c.disabled}>{c.name}</option>)}
                </select>
              </label>
            )}
          </>
        )}
      </div>
      {mode === 'super' && form.role === 'company_admin' && (
        <p className="hint">A company admin can add users and upload documents for their own company only.</p>
      )}
      {error && <p className="form-error" role="alert">{error}</p>}
      <div><button type="submit" className="btn primary" disabled={busy}>{busy ? 'Creating…' : 'Create user'}</button></div>
    </form>
  );
}

function UserEditor({ mode, user, isSelf, onSaved, onCancel }) {
  const { push } = useToast();
  const [role, setRole] = useState(user.role);
  const [disabled, setDisabled] = useState(!!user.disabled);
  const [password, setPassword] = useState('');
  const legacy = mode === 'super' && !user.company && user.role === 'user';       // purane company-less accounts
  const [text, setText] = useState((user.collections || []).join(', '));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const canRole = mode === 'super' && user.role !== 'admin' && !isSelf;

  async function save(e) {
    e.preventDefault();
    const patch = {};
    if (canRole && role !== user.role) patch.role = role;
    if (!isSelf && disabled !== !!user.disabled) patch.disabled = disabled;
    if (password) patch.password = password;
    if (legacy) {
      const list = parseCollections(text);
      if (list.some((c) => !NAME.test(c))) return setError('Collection names may use letters, numbers, . - _ and : only.');
      if (JSON.stringify(list) !== JSON.stringify(user.collections || [])) patch.collections = list;
    }
    if (!Object.keys(patch).length) return onCancel();
    setBusy(true); setError('');
    try {
      if (mode === 'super') await api.adminUpdateUser(user.username, patch);
      else await api.companyUpdateUser(user.username, patch);
      push(`Saved changes for “${user.username}”.`, 'success');
      onSaved();
    } catch (err) {
      setError(describeError(err, 'Could not save the changes'));
      setBusy(false);
    }
  }

  return (
    <form className="editor form" onSubmit={save}>
      <div className="grid2">
        {canRole && (
          <label>Role
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="user">User</option>
              <option value="company_admin">Company admin</option>
            </select>
          </label>
        )}
        <label>New password (optional)
          <input type="text" value={password} onChange={(e) => setPassword(e.target.value)} minLength={8} maxLength={128} autoComplete="off" placeholder="Leave empty to keep" />
        </label>
      </div>
      {legacy && (
        <label>Old-style access (collection names)
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="hr, finance" />
          <span className="hint">This account has no company, so it only sees the collections listed here (plus shared regulatory ones). Better: create a new user inside a company.</span>
        </label>
      )}
      {!isSelf && (
        <label className="check">
          <input type="checkbox" checked={disabled} onChange={(e) => setDisabled(e.target.checked)} />
          Disable this account (cannot sign in)
        </label>
      )}
      {isSelf && <p className="hint">You cannot change your own role or disable your own account.</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="row-actions">
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="submit" className="btn primary" disabled={busy}>{busy ? 'Saving…' : 'Save changes'}</button>
      </div>
    </form>
  );
}

export default function UsersTab({ mode, me, companies = [], initialCompany = '' }) {
  const { push } = useToast();
  const [users, setUsers] = useState(null);
  const [filter, setFilter] = useState(initialCompany);
  const [editing, setEditing] = useState(null);
  const [myCompany, setMyCompany] = useState(null);

  useEffect(() => { setFilter(initialCompany); }, [initialCompany]);

  const load = useCallback(async () => {
    try {
      if (mode === 'super') {
        setUsers(await api.adminListUsers(filter || undefined));
      } else {
        const res = await api.companyGet();
        setMyCompany(res);
        setUsers(res.users);
      }
    } catch (err) {
      if (err?.status !== 401) push(describeError(err, 'Could not load users'), 'error');
      setUsers([]);
    }
  }, [mode, filter, push]);
  useEffect(() => { load(); }, [load]);

  const names = Object.fromEntries(companies.map((c) => [c.slug, c.name]));

  return (
    <div className="tab">
      {mode === 'company' && myCompany && (
        <div className="panel">
          <h3>{myCompany.name}</h3>
          <p className="muted small">
            {myCompany.users.length}{myCompany.max_users ? ` of ${myCompany.max_users}` : ''} users. Your documents are private to this company.
          </p>
        </div>
      )}
      <CreateUser mode={mode} companies={companies} defaultCompany={filter} onCreated={load} />
      <div className="panel">
        <div className="row-main">
          <h3>Users {users ? `(${users.length})` : ''}</h3>
          {mode === 'super' && (
            <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter by company">
              <option value="">All companies</option>
              {companies.map((c) => <option key={c.slug} value={c.slug}>{c.name}</option>)}
            </select>
          )}
        </div>
        {!users && <p className="muted">Loading…</p>}
        {users && !users.length && <p className="muted">No users found.</p>}
        <ul className="rows">
          {(users || []).map((u) => {
            const isSelf = u.username.toLowerCase() === me.toLowerCase();
            const canEdit = mode === 'super' || u.role === 'user' || isSelf;
            return (
              <li key={u.username} className={u.disabled ? 'disabled' : ''}>
                <div className="row-main">
                  <div>
                    <strong>{u.username}</strong>
                    {isSelf && <span className="tag">you</span>}
                    <span className={`tag ${u.role !== 'user' ? 'tag-admin' : ''}`}>{ROLE_LABEL[u.role] || u.role}</span>
                    {mode === 'super' && <span className="tag">{u.company ? names[u.company] || u.company : u.role === 'admin' ? 'no company' : 'legacy'}</span>}
                    {u.disabled && <span className="tag tag-off">disabled</span>}
                  </div>
                  {canEdit && editing !== u.username && (
                    <button type="button" className="btn" onClick={() => setEditing(u.username)}>Edit</button>
                  )}
                </div>
                {editing === u.username && (
                  <UserEditor mode={mode} user={u} isSelf={isSelf} onCancel={() => setEditing(null)}
                    onSaved={() => { setEditing(null); load(); }} />
                )}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
