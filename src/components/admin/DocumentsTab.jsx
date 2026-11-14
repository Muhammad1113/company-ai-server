import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, describeError } from '../../lib/services.js';
import { useToast } from '../../context/Toast.jsx';
import { Confirm } from '../Modal.jsx';
import DocViewer from './DocViewer.jsx';
import {
  ALLOWED, MAX_UPLOAD_MB, REGULATORY_DEFAULTS, prefixOf, spaceOf, suffixOf, validSuffix,
} from './common.js';

const NEW = '__new__';
const docIdFromFilename = (name) =>
  (name.replace(/\.[^.]+$/, '').replace(/[^\w.\-:]/g, '_').slice(0, 200)) || 'document';

/**
 * Super admin: "space" chunta hai (Shared regulatory / ek company / legacy) phir us space ki collection.
 * Company admin: space hamesha uski apni company; kabhi doosri nahi (backend bhi 403 deta hai).
 */
export default function DocumentsTab({ mode, companySlug, companies = [], onCollectionsChanged }) {
  const { push } = useToast();
  const [collections, setCollections] = useState(null);
  const [space, setSpace] = useState(mode === 'company' ? companySlug : 'reg');
  const [pick, setPick] = useState('');
  const [newName, setNewName] = useState('');
  const [uploads, setUploads] = useState([]);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const [toDelete, setToDelete] = useState(null);
  const [viewing, setViewing] = useState(null);      // {docId, collection}
  const [docs, setDocs] = useState([]);
  const [docsState, setDocsState] = useState('idle');
  const [docsError, setDocsError] = useState('');
  const docsReq = useRef(0);
  const fileInput = useRef(null);
  const notifyRef = useRef(onCollectionsChanged);
  notifyRef.current = onCollectionsChanged;

  const load = useCallback(async () => {
    try {
      const list = await api.listCollections();
      setCollections(list);
      notifyRef.current?.(list);
    } catch (err) {
      if (err?.status !== 401) push(describeError(err, 'Could not load collections'), 'error');
      setCollections([]);
    }
  }, [push]);
  useEffect(() => { load(); }, [load]);

  // Is space ki collections: maujooda + suggestions (reg: fda/eu/uk/au, company: docs)
  const options = useMemo(() => {
    const existing = (collections || []).filter((c) => c.can_manage && spaceOf(c.name) === space);
    const map = new Map(existing.map((c) => [suffixOf(c.name), c.documents]));
    if (space === 'reg') REGULATORY_DEFAULTS.forEach((s) => { if (!map.has(s)) map.set(s, 0); });
    else if (space !== 'legacy' && !map.has('docs')) map.set('docs', 0);
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [collections, space]);

  useEffect(() => {      // space badle to pehli option
    setPick((p) => (options.some(([s]) => s === p) || p === NEW ? p : options[0]?.[0] || (space === 'legacy' ? '' : NEW)));
  }, [options, space]);

  const suffix = pick === NEW ? newName.trim().toLowerCase() : pick;
  const collection = suffix ? prefixOf(space) + suffix : '';
  const validTarget = space === 'legacy' ? !!collection : pick === NEW ? validSuffix(suffix) : !!collection;

  const loadDocs = useCallback(async (name) => {
    const id = ++docsReq.current;
    if (!name) { setDocs([]); setDocsState('idle'); return; }
    setDocsState('loading');
    try {
      const list = await api.listDocuments(name);
      if (id !== docsReq.current) return;
      setDocs(list || []);
      setDocsState('ready');
    } catch (err) {
      if (id !== docsReq.current || err?.status === 401) return;
      setDocsError(describeError(err, 'Could not load documents'));
      setDocsState('error');
    }
  }, []);
  useEffect(() => { loadDocs(pick === NEW ? '' : collection); }, [collection, pick, loadDocs]);

  async function upload(fileList) {
    const files = [...fileList];
    if (!files.length) return;
    if (!validTarget) return push('Choose a collection, or type a valid new name (lowercase letters, numbers, . - _).', 'error');
    setBusy(true);
    for (const file of files) {
      const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
      const entry = { key: `${Date.now()}-${file.name}`, name: file.name, collection, state: 'working' };
      setUploads((u) => [entry, ...u]);
      const update = (patch) => setUploads((u) => u.map((x) => (x.key === entry.key ? { ...x, ...patch } : x)));
      if (!ALLOWED.includes(ext)) { update({ state: 'error', message: `Unsupported type. Use ${ALLOWED.join(' ')}.` }); continue; }
      if (file.size > MAX_UPLOAD_MB * 1024 * 1024) { update({ state: 'error', message: `Larger than ${MAX_UPLOAD_MB} MB.` }); continue; }
      try {
        const res = await api.ingestFile(file, collection);
        update({ state: 'done', docId: res.doc_id || docIdFromFilename(file.name), chunks: res.chunks, pages: res.pages });
      } catch (err) {
        if (err?.status === 401) { setBusy(false); return; }
        update({ state: 'error', message: describeError(err, 'Upload failed') });
      }
    }
    setBusy(false);
    if (fileInput.current) fileInput.current.value = '';
    const uploadedSuffix = suffix;
    setNewName('');
    await load();
    setPick(uploadedSuffix);
    await loadDocs(collection);
  }

  async function confirmDelete() {
    const { docId, collection: col } = toDelete;
    setBusy(true);
    try {
      await api.deleteDocument(docId, col);
      push(`Deleted “${docId}” from ${col}.`, 'success');
      setUploads((u) => u.filter((x) => !(x.docId === docId && x.collection === col)));
      setToDelete(null);
      await load();
      await loadDocs(col);
    } catch (err) {
      if (err?.status !== 401) push(describeError(err, 'Could not delete the document'), 'error');
      setToDelete(null);
    } finally { setBusy(false); }
  }

  const hasLegacy = (collections || []).some((c) => c.can_manage && spaceOf(c.name) === 'legacy');
  const spaceLabel = space === 'reg' ? 'Shared regulatory (every company can read)'
    : space === 'legacy' ? 'Old collections (super admin only)' : (companies.find((c) => c.slug === space)?.name || space);

  return (
    <div className="tab">
      <div className="panel form">
        <h3>Upload documents</h3>
        <div className="grid2">
          {mode === 'super' ? (
            <label>Where
              <select value={space} onChange={(e) => { setSpace(e.target.value); setNewName(''); }}>
                <option value="reg">Shared regulatory (FDA, EU, UK, AU…)</option>
                {companies.map((c) => <option key={c.slug} value={c.slug}>{c.name}{c.disabled ? ' (disabled)' : ''}</option>)}
                {hasLegacy && <option value="legacy">Old collections</option>}
              </select>
            </label>
          ) : (
            <div className="muted small">Documents here are private to your company. Other companies can never see them.</div>
          )}
          <label>Collection
            <select value={pick} onChange={(e) => setPick(e.target.value)}>
              {options.map(([s, n]) => <option key={s} value={s}>{s} ({n})</option>)}
              {space !== 'legacy' && <option value={NEW}>+ New collection…</option>}
            </select>
          </label>
          {pick === NEW && (
            <label>New collection name
              <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder={space === 'reg' ? 'e.g. iso' : 'e.g. manuals'}
                autoCapitalize="none" maxLength={60} />
            </label>
          )}
        </div>
        {space === 'reg' && mode === 'super' && (
          <p className="hint">For EU, UK and AU rules use the <strong>Regulatory</strong> tab, so older versions are archived and can be restored. A file uploaded here with the same name simply replaces the earlier one.</p>
        )}
        <div
          className={`drop ${drag ? 'over' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files); }}
        >
          <p>Drop files here, or</p>
          <button type="button" className="btn" disabled={busy || !validTarget} onClick={() => fileInput.current?.click()}>Choose files</button>
          <input ref={fileInput} type="file" hidden multiple accept={ALLOWED.join(',')} onChange={(e) => upload(e.target.files)} />
          <p className="hint">{ALLOWED.join(' ')} up to {MAX_UPLOAD_MB} MB. Scanned PDFs without text are not supported yet.</p>
        </div>
        {collection && <p className="hint">Files will go to <strong>{collection}</strong> ({spaceLabel}). A file with the same name replaces the earlier version.</p>}

        {uploads.length > 0 && (
          <ul className="rows compact">
            {uploads.map((u) => (
              <li key={u.key}>
                <div className="row-main">
                  <div>
                    <strong dir="auto">{u.name}</strong> <span className="muted small">to {u.collection}</span>
                    {u.state === 'working' && <div className="muted small">Reading and indexing…</div>}
                    {u.state === 'done' && <div className="ok small">Added as “{u.docId}”, {u.chunks} parts{u.pages ? `, ${u.pages} pages` : ''}.</div>}
                    {u.state === 'error' && <div className="form-error small">{u.message}</div>}
                  </div>
                  {u.state === 'done' && (
                    <button type="button" className="btn danger" onClick={() => setToDelete({ docId: u.docId, collection: u.collection })}>Delete</button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="panel">
        <h3>Documents{collection && pick !== NEW ? <> in <strong>{collection}</strong></> : null}</h3>
        {docsState === 'loading' && <p className="muted">Loading…</p>}
        {docsState === 'error' && <p className="form-error small">{docsError}</p>}
        {docsState === 'idle' && <p className="muted">Choose a collection above.</p>}
        {docsState === 'ready' && !docs.length && <p className="muted">No documents in this collection yet.</p>}
        {docsState === 'ready' && docs.length > 0 && (
          <ul className="rows compact">
            {docs.map((d) => (
              <li key={d.doc_id}>
                <div className="row-main">
                  <div>
                    <strong dir="auto">{d.doc_id}</strong>
                    <div className="muted small">
                      {d.chunks} {d.chunks === 1 ? 'part' : 'parts'}
                      {d.pages ? `, ${d.pages} pages` : ''}{d.type ? `, ${String(d.type).toUpperCase()}` : ''}
                    </div>
                  </div>
                  <div className="row-actions">
                    <button type="button" className="btn" onClick={() => setViewing({ docId: d.doc_id, collection })}>View</button>
                    <button type="button" className="btn danger" disabled={busy} onClick={() => setToDelete({ docId: d.doc_id, collection })}>Delete</button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {viewing && (
        <DocViewer title={viewing.docId} onClose={() => setViewing(null)}
          load={() => api.documentContent(viewing.docId, viewing.collection).then((r) => ({ parts: r.parts }))} />
      )}
      {toDelete && (
        <Confirm
          title="Delete document?"
          message={`“${toDelete.docId}” will be removed from ${toDelete.collection}. Answers can no longer use it.`}
          onConfirm={confirmDelete}
          onClose={() => !busy && setToDelete(null)}
          busy={busy}
        />
      )}
    </div>
  );
}
