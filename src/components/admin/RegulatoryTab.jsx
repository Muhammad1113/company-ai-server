import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, describeError } from '../../lib/services.js';
import { useToast } from '../../context/Toast.jsx';
import { Confirm } from '../Modal.jsx';
import DocViewer from './DocViewer.jsx';
import { ALLOWED, MAX_UPLOAD_MB } from './common.js';

const when = (sec) => (sec ? new Date(sec * 1000).toLocaleString() : '–');
const CODE = /^[A-Za-z0-9]{3}$/;
const PART = /^[0-9]{1,4}$/;
const KEY = /^[A-Za-z0-9][\w.\-]{0,99}$/;
const VERSION = /^[A-Za-z0-9][\w.\-]{0,39}$/;
const titleOf = (c) => c.data.heading || c.data.device_name || c.data.product_description || c.data.applicant || c.key;

function Changes({ changes }) {
  return (
    <ul className="changes">
      {changes.map((c) => (
        <li key={c.key}>
          <span className={`tag ${c.change}`}>{c.change}</span> <strong dir="auto">{c.key}</strong> <span className="muted small">({c.code})</span>
          <div dir="auto">{String(titleOf(c)).slice(0, 160)}</div>
          <details>
            <summary className="muted small">Full record</summary>
            <div className="diff">
              {Object.entries(c.data).map(([k, v]) => <div key={k}><strong>{k.replace(/_/g, ' ')}:</strong> {v}</div>)}
            </div>
          </details>
          {c.fields?.map((f) => (
            <span key={f.field} className="diff">
              {f.field.replace(/_/g, ' ')}: <del>{String(f.old ?? '–').slice(0, 300)}</del> → <ins>{String(f.new ?? '–').slice(0, 300)}</ins>
            </span>
          ))}
        </li>
      ))}
    </ul>
  );
}

function OpenFda({ onPendingChanged }) {
  const { push } = useToast();
  const [status, setStatus] = useState(null);
  const [pending, setPending] = useState([]);
  const [open, setOpen] = useState(null);          // {id, changes}
  const [code, setCode] = useState('');
  const [label, setLabel] = useState('');
  const [busy, setBusy] = useState('');
  const [reject, setReject] = useState(null);
  const notify = useRef(onPendingChanged);
  notify.current = onPendingChanged;

  const load = useCallback(async () => {
    try {
      const [st, list] = await Promise.all([api.regOpenfda(), api.regPending()]);
      setStatus(st); setPending(list); notify.current?.(st.pending_count);
    } catch (err) {
      if (err?.status !== 401) push(describeError(err, 'Could not load FDA settings'), 'error');
    }
  }, [push]);
  useEffect(() => { load(); }, [load]);

  async function run(name, fn, okMessage) {
    setBusy(name);
    try { const res = await fn(); if (okMessage) push(okMessage(res), 'success'); await load(); return res; }
    catch (err) { if (err?.status !== 401) push(describeError(err, 'Action failed'), 'error'); return null; }
    finally { setBusy(''); }
  }

  const addCode = async (e) => {
    e.preventDefault();
    if (!CODE.test(code.trim())) return push('An FDA product code has 3 letters or digits, for example GAD.', 'error');
    if (await run('add', () => api.regAddCode(code.trim(), label.trim()))) { setCode(''); setLabel(''); }
  };
  const sync = () => run('sync', api.regSync, (r) => (r.changes
    ? `${r.changes} update${r.changes === 1 ? '' : 's'} found. Review them below.`
    : r.errors?.length ? 'FDA could not be reached for some codes. See the last check.' : 'No changes since the last approved data.'));
  const review = async (id) => {
    if (open?.id === id) return setOpen(null);
    try { const d = await api.regPendingDetail(id); setOpen({ id, changes: d.payload }); }
    catch (err) { push(describeError(err, 'Could not load the update'), 'error'); }
  };
  const approve = (id) => run(`ok${id}`, () => api.regApprove(id), (r) => `Approved. ${r.documents.length} FDA document(s) updated.`).then(() => setOpen(null));
  const doReject = async () => {
    const id = reject; setReject(null);
    await run(`no${id}`, () => api.regReject(id), () => 'Rejected. These exact records will not be proposed again.');
    setOpen(null);
  };

  const last = status?.last_run;
  return (
    <div className="panel">
      <h3>US FDA (openFDA)</h3>
      <p className="hint">Nothing from FDA reaches the chatbot until you approve it. Only public data is fetched: device classification, 510(k) clearances and recalls for the product codes below.</p>
      {status && (
        <>
          <ul className="chips">
            {status.codes.map((c) => (
              <li key={c.code} className="chip">
                <strong>{c.code}</strong>{c.label ? ` ${c.label}` : ''}{' '}
                <button type="button" className="link-btn" aria-label={`Remove ${c.code}`} disabled={!!busy}
                  onClick={() => run('rm', () => api.regRemoveCode(c.code))}>×</button>
              </li>
            ))}
            {!status.codes.length && <li className="muted small">No product codes yet.</li>}
          </ul>
          <form className="grid2" onSubmit={addCode}>
            <label>Product code<input value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} maxLength={3} placeholder="e.g. GAD" /></label>
            <label>Label (optional)<input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={120} placeholder="e.g. Forceps" /></label>
            <div className="row-actions">
              <button type="submit" className="btn" disabled={!!busy}>Add code</button>
              <button type="button" className="btn primary" disabled={!!busy || !status.codes.length} onClick={sync}>
                {busy === 'sync' ? 'Checking FDA…' : 'Check FDA now'}
              </button>
            </div>
          </form>
          <p className="hint">
            Last check: {when(last?.at)}{last ? ` · ${last.changes} update(s)` : ''}.{' '}
            {status.api_key_set ? 'Using your openFDA key.' : 'No openFDA key set (free limit: 1,000 requests a day per server). Each check uses 3 requests per code.'}
          </p>
          {last?.errors?.length > 0 && (
            <p className="form-error small">FDA problems: {last.errors.slice(0, 3).map((e) => `${e.code}/${e.kind}: ${e.error}`).join('; ')}</p>
          )}
        </>
      )}

      <h3>Waiting for approval</h3>
      {!pending.length && <p className="muted">Nothing waiting.</p>}
      <ul className="rows">
        {pending.map((p) => (
          <li key={p.id}>
            <div className="row-main">
              <div>
                <strong>{p.summary.new} new, {p.summary.changed} changed</strong>
                <div className="muted small">Found {when(p.created_at)} · codes: {Object.keys(p.summary.by_code).join(', ')}</div>
              </div>
              <div className="row-actions">
                <button type="button" className="btn" onClick={() => review(p.id)}>{open?.id === p.id ? 'Hide' : 'Review'}</button>
                <button type="button" className="btn primary" disabled={!!busy} onClick={() => approve(p.id)}>Approve</button>
                <button type="button" className="btn danger" disabled={!!busy} onClick={() => setReject(p.id)}>Reject</button>
              </div>
            </div>
            {open?.id === p.id && <Changes changes={open.changes} />}
          </li>
        ))}
      </ul>
      {reject && (
        <Confirm title="Reject these FDA updates?" message="They will not be added, and these exact records will not be proposed again unless FDA changes them."
          confirmLabel="Reject" onConfirm={doReject} onClose={() => setReject(null)} busy={false} />
      )}
    </div>
  );
}

function Ecfr({ onPendingChanged }) {
  const { push } = useToast();
  const [status, setStatus] = useState(null);
  const [pending, setPending] = useState([]);
  const [open, setOpen] = useState(null);          // {id, changes}
  const [part, setPart] = useState('');
  const [label, setLabel] = useState('');
  const [busy, setBusy] = useState('');
  const [reject, setReject] = useState(null);
  const notify = useRef(onPendingChanged);
  notify.current = onPendingChanged;

  const load = useCallback(async () => {
    try {
      const [st, list] = await Promise.all([api.regEcfr(), api.regEcfrPending()]);
      setStatus(st); setPending(list); notify.current?.(st.pending_count);
    } catch (err) {
      if (err?.status !== 401) push(describeError(err, 'Could not load eCFR settings'), 'error');
    }
  }, [push]);
  useEffect(() => { load(); }, [load]);

  async function run(name, fn, okMessage) {
    setBusy(name);
    try { const res = await fn(); if (okMessage) push(okMessage(res), 'success'); await load(); return res; }
    catch (err) { if (err?.status !== 401) push(describeError(err, 'Action failed'), 'error'); return null; }
    finally { setBusy(''); }
  }

  const addPart = async (e) => {
    e.preventDefault();
    if (!PART.test(part.trim())) return push('A CFR part is a number, for example 820.', 'error');
    if (await run('add', () => api.regAddPart(part.trim(), label.trim()))) { setPart(''); setLabel(''); }
  };
  const sync = () => run('sync', api.regEcfrSync, (r) => (r.changes
    ? `${r.changes} section update${r.changes === 1 ? '' : 's'} found. Review them below.`
    : r.errors?.length ? 'eCFR could not be reached for some parts. See the last check.' : 'No changes since the last approved text.'));
  const review = async (id) => {
    if (open?.id === id) return setOpen(null);
    try { const d = await api.regPendingDetail(id); setOpen({ id, changes: d.payload }); }
    catch (err) { push(describeError(err, 'Could not load the update'), 'error'); }
  };
  const approve = (id) => run(`ok${id}`, () => api.regApprove(id), (r) => `Approved. ${r.documents.length} regulation document(s) updated.`).then(() => setOpen(null));
  const doReject = async () => {
    const id = reject; setReject(null);
    await run(`no${id}`, () => api.regReject(id), () => 'Rejected. These exact sections will not be proposed again.');
    setOpen(null);
  };

  const last = status?.last_run;
  return (
    <div className="panel">
      <h3>US FDA regulations (eCFR, 21 CFR)</h3>
      <p className="hint">The actual US law text from ecfr.gov (free US Government data, no key needed). Nothing reaches the chatbot until you approve it. Each part is read section by section, so Review shows which sections are new, changed or removed.</p>
      {status && (
        <>
          <ul className="chips">
            {status.parts.map((c) => (
              <li key={c.part} className="chip">
                <strong>21 CFR {c.part}</strong>{c.label ? ` ${c.label}` : ''}{' '}
                <button type="button" className="link-btn" aria-label={`Remove part ${c.part}`} disabled={!!busy}
                  onClick={() => run('rm', () => api.regRemovePart(c.part))}>×</button>
              </li>
            ))}
            {!status.parts.length && <li className="muted small">No CFR parts yet.</li>}
          </ul>
          <form className="grid2" onSubmit={addPart}>
            <label>CFR part<input value={part} onChange={(e) => setPart(e.target.value.replace(/\D/g, ''))} maxLength={4} placeholder="e.g. 820" /></label>
            <label>Label (optional)<input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={120} placeholder="e.g. QMSR" /></label>
            <div className="row-actions">
              <button type="submit" className="btn" disabled={!!busy}>Add part</button>
              <button type="button" className="btn primary" disabled={!!busy || !status.parts.length} onClick={sync}>
                {busy === 'sync' ? 'Checking eCFR…' : 'Check eCFR now'}
              </button>
            </div>
          </form>
          <p className="hint">
            Last check: {when(last?.at)}{last ? ` · ${last.changes} update(s)` : ''}{last?.as_of ? ` · law text current as of ${last.as_of}` : ''}.
          </p>
          {last?.errors?.length > 0 && (
            <p className="form-error small">eCFR problems: {last.errors.slice(0, 3).map((e) => `${e.code}: ${e.error}`).join('; ')}</p>
          )}
        </>
      )}

      <h3>Waiting for approval</h3>
      {!pending.length && <p className="muted">Nothing waiting.</p>}
      <ul className="rows">
        {pending.map((p) => (
          <li key={p.id}>
            <div className="row-main">
              <div>
                <strong>{p.summary.new} new, {p.summary.changed} changed{p.summary.removed ? `, ${p.summary.removed} removed` : ''}</strong>
                <div className="muted small">Found {when(p.created_at)} · {Object.keys(p.summary.by_code).join(', ')}{p.summary.as_of ? ` · current as of ${p.summary.as_of}` : ''}</div>
              </div>
              <div className="row-actions">
                <button type="button" className="btn" onClick={() => review(p.id)}>{open?.id === p.id ? 'Hide' : 'Review'}</button>
                <button type="button" className="btn primary" disabled={!!busy} onClick={() => approve(p.id)}>Approve</button>
                <button type="button" className="btn danger" disabled={!!busy} onClick={() => setReject(p.id)}>Reject</button>
              </div>
            </div>
            {open?.id === p.id && <Changes changes={open.changes} />}
          </li>
        ))}
      </ul>
      {reject && (
        <Confirm title="Reject these eCFR updates?" message="They will not be added, and these exact sections will not be proposed again unless the law text changes."
          confirmLabel="Reject" onConfirm={doReject} onClose={() => setReject(null)} busy={false} />
      )}
    </div>
  );
}

function Versions() {
  const { push } = useToast();
  const [versions, setVersions] = useState(null);
  const [collection, setCollection] = useState('eu');
  const [custom, setCustom] = useState('');
  const [docKey, setDocKey] = useState('');
  const [version, setVersion] = useState('');
  const [effectiveDate, setEffectiveDate] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [note, setNote] = useState('');
  const [archiveOnly, setArchiveOnly] = useState(false);
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(null);       // {kind: 'restore'|'retire', v}
  const [viewing, setViewing] = useState(null);
  const input = useRef(null);

  const load = useCallback(async () => {
    try { setVersions(await api.regVersions()); }
    catch (err) { if (err?.status !== 401) push(describeError(err, 'Could not load versions'), 'error'); setVersions([]); }
  }, [push]);
  useEffect(() => { load(); }, [load]);

  const target = collection === '__new__' ? `reg__${custom.trim().toLowerCase()}` : `reg__${collection}`;
  const groups = useMemo(() => {
    const m = new Map();
    (versions || []).forEach((v) => {
      const k = `${v.collection} / ${v.doc_key}`;
      if (!m.has(k)) m.set(k, []);
      m.get(k).push(v);
    });
    return [...m.entries()];
  }, [versions]);

  async function submit(e) {
    e.preventDefault();
    if (!/^reg__[a-z0-9][a-z0-9_.\-]*$/.test(target) || target.includes('____')) return push('Choose a valid collection name.', 'error');
    if (!KEY.test(docKey.trim())) return push('Document name: letters, numbers, . _ - (no spaces), for example eu-mdr-2017-745.', 'error');
    if (!VERSION.test(version.trim())) return push('Version label: letters, numbers, . _ - (no spaces), for example 2026-05.', 'error');
    if (!file) return push('Choose a file.', 'error');
    const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
    if (!ALLOWED.includes(ext)) return push(`Unsupported type. Use ${ALLOWED.join(' ')}.`, 'error');
    if (file.size > MAX_UPLOAD_MB * 1024 * 1024) return push(`Larger than ${MAX_UPLOAD_MB} MB.`, 'error');
    setBusy(true);
    try {
      const res = await api.regUploadVersion(file, {
        collection: target, docKey: docKey.trim(), version: version.trim(), effectiveDate, sourceUrl: sourceUrl.trim(), note: note.trim(), archiveOnly,
      });
      push(archiveOnly ? `Saved ${res.version} to the archive only.` : `${res.version} is now the active version. The earlier one is archived.`, 'success');
      setVersion(''); setEffectiveDate(''); setSourceUrl(''); setNote(''); setFile(null); setArchiveOnly(false);
      if (input.current) input.current.value = '';
      await load();
    } catch (err) { if (err?.status !== 401) push(describeError(err, 'Upload failed'), 'error'); }
    finally { setBusy(false); }
  }

  async function doConfirm() {
    const { kind, v } = confirm;
    setBusy(true);
    try {
      await (kind === 'restore' ? api.regRestore(v.id) : api.regRetire(v.id));
      push(kind === 'restore' ? `Version ${v.version} is active again.` : `Version ${v.version} removed from answers (kept in the archive).`, 'success');
      await load();
    } catch (err) { if (err?.status !== 401) push(describeError(err, 'Action failed'), 'error'); }
    finally { setBusy(false); setConfirm(null); }
  }

  const known = [...new Set(['eu', 'uk', 'au', ...(versions || []).map((v) => v.collection.slice(5))])];

  return (
    <div className="panel form">
      <h3>Versioned documents (EU, UK, AU…)</h3>
      <p className="hint">The UK (MHRA) and Australia (TGA) publish no free official feed, so these are uploaded by hand, like the EU. A new version replaces the old one in answers; the old one stays in the archive and can be restored.</p>
      <form className="form" onSubmit={submit}>
        <div className="grid2">
          <label>Region
            <select value={collection} onChange={(e) => setCollection(e.target.value)}>
              {known.map((k) => <option key={k} value={k}>{k.toUpperCase()} (reg__{k})</option>)}
              <option value="__new__">+ Other…</option>
            </select>
          </label>
          {collection === '__new__' && <label>Region name<input value={custom} onChange={(e) => setCustom(e.target.value)} maxLength={40} placeholder="e.g. iso" /></label>}
          <label>Document name<input value={docKey} onChange={(e) => setDocKey(e.target.value)} maxLength={100} placeholder="e.g. eu-mdr-2017-745" /></label>
          <label>Version label<input value={version} onChange={(e) => setVersion(e.target.value)} maxLength={40} placeholder="e.g. 2026-05" /></label>
          <label>Effective date (optional)<input type="date" value={effectiveDate} onChange={(e) => setEffectiveDate(e.target.value)} /></label>
          <label>Source link (optional)<input value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} maxLength={500} placeholder="https://…" /></label>
          <label>Note (optional)<input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} /></label>
        </div>
        <label className="check"><input type="checkbox" checked={archiveOnly} onChange={(e) => setArchiveOnly(e.target.checked)} />
          This is an older version: archive it, do not use it in answers
        </label>
        <div className="row-actions">
          <input ref={input} type="file" accept={ALLOWED.join(',')} onChange={(e) => setFile(e.target.files?.[0] || null)} />
          <button type="submit" className="btn primary" disabled={busy}>{busy ? 'Uploading…' : 'Upload version'}</button>
        </div>
        <p className="hint">{ALLOWED.join(' ')} up to {MAX_UPLOAD_MB} MB. Uploading to <strong>{target}</strong>; every company can read it.</p>
      </form>

      {versions && !groups.length && <p className="muted">No versioned documents yet.</p>}
      {groups.map(([name, list]) => (
        <div key={name}>
          <h4 dir="auto">{name}</h4>
          <ul className="rows compact">
            {list.map((v) => (
              <li key={v.id}>
                <div className="row-main">
                  <div>
                    <strong dir="auto">{v.version}</strong> <span className={`tag ${v.status}`}>{v.status}</span>
                    <div className="muted small">
                      {v.effective_date ? `Effective ${v.effective_date} · ` : ''}uploaded {when(v.uploaded_at)}{v.uploaded_by ? ` by ${v.uploaded_by}` : ''} · {v.filename}
                      {v.note ? ` · ${v.note}` : ''}
                    </div>
                  </div>
                  <div className="row-actions">
                    <button type="button" className="btn" onClick={() => setViewing(v)}>View</button>
                    {v.status !== 'active' && <button type="button" className="btn" disabled={busy} onClick={() => setConfirm({ kind: 'restore', v })}>Make active</button>}
                    {v.status === 'active' && <button type="button" className="btn danger" disabled={busy} onClick={() => setConfirm({ kind: 'retire', v })}>Retire</button>}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {viewing && (
        <DocViewer title={`${viewing.doc_key} (${viewing.version})`} onClose={() => setViewing(null)}
          load={() => api.regVersionText(viewing.id).then((r) => ({ text: r.text }))} />
      )}
      {confirm && (
        <Confirm
          title={confirm.kind === 'restore' ? 'Make this version active?' : 'Retire this version?'}
          message={confirm.kind === 'restore'
            ? `Version ${confirm.v.version} will be used in answers. The current active version moves to the archive.`
            : `Answers will stop using ${confirm.v.doc_key} until you activate a version again. The file stays in the archive.`}
          confirmLabel={confirm.kind === 'restore' ? 'Make active' : 'Retire'}
          onConfirm={doConfirm} onClose={() => !busy && setConfirm(null)} busy={busy} />
      )}
    </div>
  );
}

export default function RegulatoryTab({ onPendingChanged }) {
  return (
    <div className="tab">
      <OpenFda onPendingChanged={onPendingChanged} />
      <Ecfr onPendingChanged={onPendingChanged} />
      <Versions />
    </div>
  );
}
