import { useCallback, useEffect, useState } from 'react';
import { api, describeError } from '../../lib/services.js';
import { AUDIT_GROUPS, actionLabel, actorLabel, detailText } from '../../lib/audit.js';

const PAGE = 50;
const fmtTime = (ts) => new Date(ts * 1000).toLocaleString();

// Super admin: kaun ne kya kiya (passwords aur document ka text kabhi nahi). Naye pehle.
export default function AuditTab() {
  const [events, setEvents] = useState(null);
  const [hasMore, setHasMore] = useState(false);
  const [filters, setFilters] = useState({ action: '', actor: '', failedOnly: false });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const params = useCallback((offset) => {
    const p = { limit: PAGE, offset };
    if (filters.action) p.action = filters.action;
    if (filters.actor.trim()) p.actor = filters.actor.trim();
    if (filters.failedOnly) p.ok = 'false';
    return p;
  }, [filters]);

  const load = useCallback(async () => {
    setBusy(true); setError('');
    try {
      const r = await api.adminAudit(params(0));
      setEvents(r.events); setHasMore(r.has_more);
    } catch (err) {
      setError(describeError(err, 'Could not load the audit log'));
    } finally { setBusy(false); }
  }, [params]);

  useEffect(() => { load(); }, [load]);

  async function more() {
    setBusy(true);
    try {
      const r = await api.adminAudit(params(events.length));
      setEvents((cur) => [...cur, ...r.events]); setHasMore(r.has_more);
    } catch (err) {
      setError(describeError(err, 'Could not load more'));
    } finally { setBusy(false); }
  }

  return (
    <div className="panel">
      <h3>Audit log</h3>
      <p className="muted small">Who did what, and when. Passwords and document or chat content are never recorded.
        Entries older than the server&apos;s retention period are removed automatically.</p>
      <div className="grid2">
        <label>Type
          <select value={filters.action} onChange={(e) => setFilters({ ...filters, action: e.target.value })}>
            {AUDIT_GROUPS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
        </label>
        <label>Person
          <input value={filters.actor} onChange={(e) => setFilters({ ...filters, actor: e.target.value })}
            placeholder="username" autoCapitalize="none" maxLength={64} />
        </label>
      </div>
      <label className="check">
        <input type="checkbox" checked={filters.failedOnly}
          onChange={(e) => setFilters({ ...filters, failedOnly: e.target.checked })} /> Failed attempts only
      </label>
      {error && <p className="error" role="alert">{error}</p>}
      {!events && !error && <p className="muted">Loading…</p>}
      {events && !events.length && <p className="muted">No events match.</p>}
      {events && events.length > 0 && (
        <div className="table-wrap">
          <table className="audit-table">
            <thead><tr><th>When</th><th>Who</th><th>What</th><th>On</th><th>Details</th><th>From</th></tr></thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id} className={e.ok ? '' : 'audit-failed'}>
                  <td>{fmtTime(e.ts)}</td>
                  <td>{actorLabel(e)}</td>
                  <td>{actionLabel(e.action)}{e.ok ? '' : ' (failed)'}</td>
                  <td>{e.target || '–'}</td>
                  <td className="muted small">{detailText(e.detail) || '–'}</td>
                  <td className="muted small">{e.ip || '–'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="row">
        <button type="button" className="btn" onClick={load} disabled={busy}>Refresh</button>
        {hasMore && <button type="button" className="btn" onClick={more} disabled={busy}>Load more</button>}
      </div>
    </div>
  );
}
