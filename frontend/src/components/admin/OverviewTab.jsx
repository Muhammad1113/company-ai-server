import { useEffect, useMemo, useRef, useState } from 'react';
import { api, describeError } from '../../lib/services.js';
import { avatarFor } from '../../lib/avatars.js';
import { initials } from '../../lib/format.js';
import { fmtIdle, fmtMs } from './common.js';

const WINDOWS = [[1, 'Last hour'], [24, 'Last 24 hours'], [168, 'Last 7 days']];
const REFRESH_MS = 15000;
const reduced = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

// Number ko purani value se nayi tak ginta hua dikhata hai (animation band ho to seedha).
function useCountUp(target, ms = 650) {
  const [shown, setShown] = useState(0);
  const from = useRef(0);
  useEffect(() => {
    if (reduced() || from.current === target) { from.current = target; setShown(target); return undefined; }
    const start = performance.now();
    const a = from.current;
    let raf;
    const step = (t) => {
      const p = Math.min(1, (t - start) / ms);
      setShown(Math.round(a + (target - a) * (1 - (1 - p) ** 3)));
      if (p < 1) raf = requestAnimationFrame(step); else from.current = target;
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return shown;
}

function Stat({ label, value, sub, hint, onClick, live }) {
  const n = useCountUp(value);
  return (
    <button type="button" className="stat stat-btn" onClick={onClick} title={hint}>
      <span className="stat-top">
        <span className="stat-value">{n}</span>
        {live && value > 0 && <span className="live-dot" aria-hidden="true" />}
      </span>
      <span className="stat-label">{label}</span>
      {sub && <span className="muted small">{sub}</span>}
      <span className="stat-go" aria-hidden="true">{hint} →</span>
    </button>
  );
}

function Meter({ value, max, warn }) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return (
    <div className="meter" role="img" aria-label={`${value} of ${max}`}>
      <div className={`meter-fill ${warn ? 'warn' : ''}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

function Who({ name }) {
  const a = avatarFor(name);
  return a
    ? <img className="avatar photo mini" src={a.src} alt="" width="28" height="28" />
    : <span className="avatar small mini" aria-hidden="true">{initials(name)}</span>;
}

const COLS = [['name', 'Company'], ['users', 'Users'], ['online', 'Online'], ['requests', 'Requests'], ['errors', 'Errors'], ['avg_latency_ms', 'Avg time']];

export default function OverviewTab({ onOpenCompany, onOpenUsers, onNavigate }) {
  const [hours, setHours] = useState(24);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [updated, setUpdated] = useState(null);
  const [live, setLive] = useState(true);
  const [loading, setLoading] = useState(false);
  const [nonce, setNonce] = useState(0);
  const [sort, setSort] = useState({ key: 'requests', dir: 'desc' });
  const [flash, setFlash] = useState('');
  const alive = useRef(true);
  const onlineRef = useRef(null);
  const tableRef = useRef(null);

  useEffect(() => {
    alive.current = true;
    let timer;
    let ctl;
    async function tick() {
      if (document.visibilityState === 'hidden') { timer = setTimeout(tick, REFRESH_MS); return; }
      ctl = new AbortController();
      setLoading(true);
      try {
        const res = await api.adminOverview(hours, ctl.signal);
        if (!alive.current) return;
        setData(res); setError(''); setUpdated(new Date());
      } catch (err) {
        if (err?.name === 'AbortError' || err?.status === 401 || !alive.current) return;
        setError(describeError(err, 'Could not load the overview'));
      } finally {
        if (alive.current) setLoading(false);
      }
      if (alive.current && live) timer = setTimeout(tick, REFRESH_MS);
    }
    tick();
    return () => { alive.current = false; clearTimeout(timer); ctl?.abort(); };
  }, [hours, live, nonce]);

  const rows = useMemo(() => {
    const list = [...(data?.companies ?? [])];
    const { key, dir } = sort;
    list.sort((a, b) => {
      const x = key === 'name' ? a.name.toLowerCase() : (a[key] ?? -1);
      const y = key === 'name' ? b.name.toLowerCase() : (b[key] ?? -1);
      return (x < y ? -1 : x > y ? 1 : 0) * (dir === 'asc' ? 1 : -1);
    });
    return list;
  }, [data, sort]);

  const maxRequests = useMemo(() => Math.max(1, ...(data?.companies ?? []).map((c) => c.requests)), [data]);

  const jump = (ref, name) => {
    ref.current?.scrollIntoView({ behavior: reduced() ? 'auto' : 'smooth', block: 'center' });
    setFlash(name);
    setTimeout(() => setFlash(''), 1400);
  };
  const sortBy = (key) => setSort((s) => (s.key === key
    ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
    : { key, dir: key === 'name' ? 'asc' : 'desc' }));

  if (!data) return <div className="tab"><p className={error ? 'form-error' : 'muted'}>{error || 'Loading…'}</p></div>;

  const q = data.queue;
  const busy = q.waiting > 0 || (q.max_concurrent > 0 && q.active >= q.max_concurrent);
  const state = q.waiting > 0 ? ['Queue building', 'warn'] : busy ? ['At capacity', 'warn'] : q.active > 0 ? ['Working', 'ok'] : ['Idle', 'idle'];

  return (
    <div className="tab">
      <div className="panel">
        <div className="row-main ov-head">
          <h3>Overview</h3>
          <div className="seg" role="group" aria-label="Time window">
            {WINDOWS.map(([h, l]) => (
              <button key={h} type="button" className={hours === h ? 'on' : ''} aria-pressed={hours === h} onClick={() => setHours(h)}>{l}</button>
            ))}
          </div>
        </div>
        <div className="stats">
          <Stat label="Companies" value={data.totals.companies} hint="Open companies" onClick={() => onNavigate?.('companies')} />
          <Stat label="Users" value={data.totals.users} hint="Open users" onClick={() => onNavigate?.('users')} />
          <Stat label="Online now" value={data.totals.online} sub="active in the last 5 min" hint="See who" live onClick={() => jump(onlineRef, 'online')} />
          <Stat label="Requests" value={data.totals.requests} hint="By company" onClick={() => { setSort({ key: 'requests', dir: 'desc' }); jump(tableRef, 'companies'); }} />
        </div>
        {error && <p className="form-error small" role="alert">{error} (showing the last data)</p>}
        <div className="ov-foot">
          <p className="hint">
            Counts and timings only. Questions and answers of a company are never shown here.
            {updated && ` Updated ${updated.toLocaleTimeString()}.`}
          </p>
          <div className="ov-tools">
            <button type="button" className={`pill ${live ? 'live' : ''}`} aria-pressed={live} onClick={() => setLive((v) => !v)}
              title={live ? 'Refreshing every 15 seconds. Click to pause.' : 'Paused. Click to resume.'}>
              <span className="live-dot" aria-hidden="true" />{live ? 'Live' : 'Paused'}
            </button>
            <button type="button" className="btn" disabled={loading} onClick={() => setNonce((n) => n + 1)}>{loading ? 'Refreshing…' : 'Refresh'}</button>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="row-main">
          <h3>Model load</h3>
          <span className={`status-pill ${state[1]}`}>{state[0]}</span>
        </div>
        <div className="load-row">
          <span>Running now</span><Meter value={q.active} max={q.max_concurrent} warn={busy} />
          <strong>{q.active} / {q.max_concurrent}</strong>
        </div>
        <div className="load-row">
          <span>Waiting in queue</span><Meter value={q.waiting} max={q.max_queue_size} warn={q.waiting > 0} />
          <strong>{q.waiting} / {q.max_queue_size}</strong>
        </div>
        <p className="hint">GPU power and temperature will appear on the Grafana dashboard.</p>
      </div>

      <div className={`panel ${flash === 'companies' ? 'flash' : ''}`} ref={tableRef}>
        <h3>Companies</h3>
        {!data.companies.length && <p className="muted">No companies yet. Create the first one in the Companies tab.</p>}
        {data.companies.length > 0 && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  {COLS.map(([key, label]) => (
                    <th key={key} aria-sort={sort.key === key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                      <button type="button" className="th-btn" onClick={() => sortBy(key)}>
                        {label}<span className="th-arrow" aria-hidden="true">{sort.key === key ? (sort.dir === 'asc' ? '▲' : '▼') : ''}</span>
                      </button>
                    </th>
                  ))}
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.slug} className={c.disabled ? 'off' : ''}>
                    <td>
                      <button type="button" className="link-btn strong" onClick={() => onOpenCompany?.(c.slug)}>{c.name}</button>
                      {c.disabled && <span className="tag tag-off">disabled</span>}
                    </td>
                    <td>{c.users}</td>
                    <td>{c.online > 0 ? <span className="on-now"><span className="live-dot" aria-hidden="true" />{c.online}</span> : c.online}</td>
                    <td>
                      <div className="req-cell">
                        <span>{c.requests}</span>
                        <span className="req-bar" aria-hidden="true"><span style={{ width: `${Math.round((c.requests / maxRequests) * 100)}%` }} /></span>
                      </div>
                    </td>
                    <td className={c.errors ? 'bad' : ''}>{c.errors}</td>
                    <td>{fmtMs(c.avg_latency_ms)}</td>
                    <td className="end"><button type="button" className="link-btn" onClick={() => onOpenUsers?.(c.slug)}>Users</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data.other_requests.requests > 0 && (
          <p className="hint">Not counted above: {data.other_requests.requests} requests by super admins and API keys.</p>
        )}
      </div>

      <div className={`panel ${flash === 'online' ? 'flash' : ''}`} ref={onlineRef}>
        <h3>Online now {data.online.length ? `(${data.online.length})` : ''}</h3>
        {!data.online.length && <p className="muted">Nobody is active right now.</p>}
        <ul className="rows compact">
          {data.online.map((o) => (
            <li key={o.username}>
              <div className="row-main">
                <div className="who">
                  <Who name={o.username} />
                  <strong>{o.username}</strong>
                  <span className="tag">{o.company || 'super admin'}</span>
                </div>
                <span className="muted small">{fmtIdle(o.idle_seconds)}</span>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
