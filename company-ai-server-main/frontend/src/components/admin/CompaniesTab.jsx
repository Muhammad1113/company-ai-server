import { useCallback, useEffect, useState } from 'react';
import { api, describeError } from '../../lib/services.js';
import { useToast } from '../../context/Toast.jsx';
import { Confirm } from '../Modal.jsx';
import { SLUG } from './common.js';

function CreateCompany({ onCreated }) {
  const { push } = useToast();
  const [form, setForm] = useState({ slug: '', name: '', email: '', max_users: 0 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit(e) {
    e.preventDefault();
    const slug = form.slug.trim().toLowerCase();
    if (!SLUG.test(slug) || slug.length < 2 || slug.length > 32) {
      return setError('Short name: 2 to 32 characters, lowercase letters and numbers, with single underscores between words.');
    }
    setBusy(true); setError('');
    try {
      await api.adminCreateCompany({
        slug, name: form.name.trim(), email: form.email.trim() || null, max_users: Number(form.max_users) || 0,
      });
      push(`Company “${form.name.trim()}” created.`, 'success');
      setForm({ slug: '', name: '', email: '', max_users: 0 });
      onCreated();
    } catch (err) {
      setError(describeError(err, 'Could not create the company'));
    } finally { setBusy(false); }
  }

  return (
    <form className="panel form" onSubmit={submit}>
      <h3>New company</h3>
      <div className="grid2">
        <label>Company name
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} maxLength={120} required />
        </label>
        <label>Short name (cannot be changed later)
          <input value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })}
            placeholder="e.g. acme" autoCapitalize="none" minLength={2} maxLength={32} required />
        </label>
        <label>Contact email
          <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} maxLength={200} />
        </label>
        <label>User limit (0 = unlimited)
          <input type="number" min={0} max={10000} value={form.max_users} onChange={(e) => setForm({ ...form, max_users: e.target.value })} />
        </label>
      </div>
      <p className="hint">Documents of this company will live in <strong>co_{form.slug.trim().toLowerCase() || 'short_name'}__docs</strong> and no other company can see them.</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div><button type="submit" className="btn primary" disabled={busy}>{busy ? 'Creating…' : 'Create company'}</button></div>
    </form>
  );
}

function CompanyEditor({ company, onSaved, onCancel }) {
  const { push } = useToast();
  const [name, setName] = useState(company.name);
  const [email, setEmail] = useState(company.email || '');
  const [maxUsers, setMaxUsers] = useState(company.max_users);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function save(e) {
    e.preventDefault();
    const patch = {};
    if (name.trim() !== company.name) patch.name = name.trim();
    if (email.trim() !== (company.email || '')) patch.email = email.trim() || null;
    if (Number(maxUsers) !== company.max_users) patch.max_users = Number(maxUsers) || 0;
    if (!Object.keys(patch).length) return onCancel();
    setBusy(true); setError('');
    try {
      await api.adminUpdateCompany(company.slug, patch);
      push(`Saved “${company.slug}”.`, 'success');
      onSaved();
    } catch (err) {
      setError(describeError(err, 'Could not save the changes'));
      setBusy(false);
    }
  }

  return (
    <form className="editor form" onSubmit={save}>
      <div className="grid2">
        <label>Company name<input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required /></label>
        <label>Contact email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={200} /></label>
        <label>User limit (0 = unlimited)<input type="number" min={0} max={10000} value={maxUsers} onChange={(e) => setMaxUsers(e.target.value)} /></label>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="row-actions">
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="submit" className="btn primary" disabled={busy}>{busy ? 'Saving…' : 'Save changes'}</button>
      </div>
    </form>
  );
}

export default function CompaniesTab({ focus, onOpenUsers, onChanged }) {
  const { push } = useToast();
  const [companies, setCompanies] = useState(null);
  const [editing, setEditing] = useState(focus || null);
  const [toggle, setToggle] = useState(null);       // company jise enable/disable karna hai
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const list = await api.adminListCompanies();
      setCompanies(list);
      onChanged?.(list);
    } catch (err) {
      if (err?.status !== 401) push(describeError(err, 'Could not load companies'), 'error');
      setCompanies([]);
    }
  }, [push, onChanged]);
  useEffect(() => { load(); }, [load]);

  async function confirmToggle() {
    setBusy(true);
    try {
      await api.adminUpdateCompany(toggle.slug, { disabled: !toggle.disabled });
      push(toggle.disabled ? `“${toggle.name}” enabled.` : `“${toggle.name}” disabled. Everyone in it was signed out.`, 'success');
      setToggle(null);
      await load();
    } catch (err) {
      if (err?.status !== 401) push(describeError(err, 'Could not change the status'), 'error');
      setToggle(null);
    } finally { setBusy(false); }
  }

  return (
    <div className="tab">
      <CreateCompany onCreated={load} />
      <div className="panel">
        <h3>Companies {companies ? `(${companies.length})` : ''}</h3>
        {!companies && <p className="muted">Loading…</p>}
        {companies && !companies.length && <p className="muted">No companies yet.</p>}
        <ul className="rows">
          {(companies || []).map((c) => (
            <li key={c.slug} className={c.disabled ? 'disabled' : ''}>
              <div className="row-main">
                <div>
                  <strong>{c.name}</strong>
                  <span className="tag">{c.slug}</span>
                  {c.disabled && <span className="tag tag-off">disabled</span>}
                  <div className="muted small">
                    {c.users} {c.users === 1 ? 'user' : 'users'}{c.max_users ? ` of ${c.max_users}` : ''} · {c.online} online
                    {c.email ? ` · ${c.email}` : ''}
                  </div>
                  <div className="muted small">Documents: {c.collection}</div>
                </div>
                <div className="row-actions">
                  <button type="button" className="btn" onClick={() => onOpenUsers?.(c.slug)}>Users</button>
                  {editing !== c.slug && <button type="button" className="btn" onClick={() => setEditing(c.slug)}>Edit</button>}
                  <button type="button" className={`btn ${c.disabled ? '' : 'danger'}`} onClick={() => setToggle(c)}>{c.disabled ? 'Enable' : 'Disable'}</button>
                </div>
              </div>
              {editing === c.slug && (
                <CompanyEditor company={c} onCancel={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />
              )}
            </li>
          ))}
        </ul>
      </div>
      {toggle && (
        <Confirm
          title={toggle.disabled ? 'Enable company?' : 'Disable company?'}
          message={toggle.disabled
            ? `Users of “${toggle.name}” will be able to sign in again.`
            : `Everyone in “${toggle.name}” is signed out immediately and cannot sign in until you enable it again. Their documents are kept.`}
          confirmLabel={toggle.disabled ? 'Enable' : 'Disable'}
          onConfirm={confirmToggle}
          onClose={() => !busy && setToggle(null)}
          busy={busy}
        />
      )}
    </div>
  );
}
