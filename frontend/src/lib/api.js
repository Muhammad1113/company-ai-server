import { iterateSse } from './sse.js';
import { normalizeEvent } from './chatState.js';

export class ApiError extends Error {
  constructor(status, detail, retryAfter = null) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.retryAfter = retryAfter;
  }
}

export function detailOf(payload, fallback) {
  const d = payload && payload.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) {
    return d.map((e) => (Array.isArray(e.loc) ? `${e.loc.slice(1).join('.')}: ` : '') + e.msg).join('; ');
  }
  if (d && typeof d === 'object') return d.message || d.status || fallback;
  return fallback;
}

export function createApi({ baseUrl = '/api/v1', getToken = () => null, onUnauthorized, fetchImpl } = {}) {
  const doFetch = (...args) => (fetchImpl || globalThis.fetch)(...args);

  async function request(method, path, { body, signal, auth = true, raw, headers } = {}) {
    const h = { ...(headers || {}) };
    const token = auth ? getToken() : null;
    if (token) h.Authorization = `Bearer ${token}`;
    let payload = raw;
    if (body !== undefined) {
      h['Content-Type'] = 'application/json';
      payload = JSON.stringify(body);
    }
    let res;
    try {
      res = await doFetch(baseUrl + path, { method, headers: h, body: payload, signal });
    } catch (err) {
      if (err && err.name === 'AbortError') throw err;
      throw new ApiError(0, 'Cannot reach the server. Check your connection.');
    }
    if (!res.ok) throw await toError(res, auth);
    return res;
  }

  async function toError(res, auth) {
    let data = null;
    try { data = await res.json(); } catch { /* not json */ }
    const ra = res.headers.get('Retry-After');
    const err = new ApiError(res.status, detailOf(data, `Request failed (${res.status})`), ra ? Number(ra) : null);
    if (res.status === 401 && auth && onUnauthorized) onUnauthorized(err);
    return err;
  }

  const json = async (method, path, opts) => (await request(method, path, opts)).json();
  const q = encodeURIComponent;

  return {
    // auth
    login: (username, password) => json('POST', '/auth/login', { body: { username, password }, auth: false }),
    me: () => json('GET', '/auth/me'),
    logout: () => json('POST', '/auth/logout').catch(() => null),
    changePassword: (current_password, new_password) =>
      json('POST', '/auth/change-password', { body: { current_password, new_password } }),

    // conversations
    listConversations: () => json('GET', '/conversations?limit=200').then((r) => r.conversations),
    createConversation: (title) => json('POST', '/conversations', { body: title ? { title } : {} }),
    getConversation: (id) => json('GET', `/conversations/${id}`),
    renameConversation: (id, title) => json('PATCH', `/conversations/${id}`, { body: { title } }),
    deleteConversation: (id) => json('DELETE', `/conversations/${id}`),

    // chat (SSE). onEvent({type: sources|token|error|done})
    async chatStream(id, body, { signal, onEvent }) {
      const res = await request('POST', `/conversations/${id}/chat`, { body, signal });
      for await (const raw of iterateSse(res.body)) {
        const ev = normalizeEvent(raw);
        if (ev) onEvent(ev);
      }
    },

    // rag / admin
    listCollections: () => json('GET', '/rag/collections').then((r) => r.collections),
    listDocuments: (collection) => json('GET', `/rag/documents?collection=${q(collection)}`).then((r) => r.documents),
    documentContent: (docId, collection) => json('GET', `/rag/documents/${q(docId)}/content?collection=${q(collection)}`),
    deleteDocument: (docId, collection) =>
      json('DELETE', `/rag/documents/${q(docId)}?collection=${q(collection)}`),
    ingestFile: (file, collection) =>
      json('POST', `/rag/ingest-file?filename=${q(file.name)}&collection=${q(collection)}`, {
        raw: file,
        headers: { 'Content-Type': file.type || 'application/octet-stream' },
      }),
    adminListUsers: (company) => json('GET', `/admin/users${company ? `?company=${q(company)}` : ''}`).then((r) => r.users),
    adminCreateUser: (body) => json('POST', '/admin/users', { body }),
    adminUpdateUser: (username, body) => json('PATCH', `/admin/users/${q(username)}`, { body }),

    // super admin: companies + overview (sirf ginti/timing, chat content nahi)
    adminListCompanies: () => json('GET', '/admin/companies').then((r) => r.companies),
    adminCreateCompany: (body) => json('POST', '/admin/companies', { body }),
    adminUpdateCompany: (slug, body) => json('PATCH', `/admin/companies/${q(slug)}`, { body }),
    adminAudit: (params = {}) => json('GET', `/admin/audit?${new URLSearchParams(params)}`),
    adminOverview: (hours = 24, signal) => json('GET', `/admin/overview?hours=${hours}`, { signal }),

    // super admin: regulatory (versioned documents + openFDA approval)
    regVersions: () => json('GET', '/admin/regulatory/versions').then((r) => r.versions),
    regUploadVersion: (file, p) => {
      const qs = Object.entries({
        filename: file.name, collection: p.collection, doc_key: p.docKey, version: p.version,
        effective_date: p.effectiveDate, source_url: p.sourceUrl, note: p.note, archive_only: p.archiveOnly ? 'true' : '',
      }).filter(([, v]) => v).map(([k, v]) => `${k}=${q(v)}`).join('&');
      return json('POST', `/admin/regulatory/versions?${qs}`, {
        raw: file, headers: { 'Content-Type': file.type || 'application/octet-stream' },
      });
    },
    regVersionText: (id) => json('GET', `/admin/regulatory/versions/${id}/text`),
    regRestore: (id) => json('POST', `/admin/regulatory/versions/${id}/restore`),
    regRetire: (id) => json('POST', `/admin/regulatory/versions/${id}/retire`),
    regOpenfda: () => json('GET', '/admin/regulatory/openfda'),
    regAddCode: (code, label) => json('POST', '/admin/regulatory/openfda/codes', { body: { code, label: label || null } }),
    regRemoveCode: (code) => json('DELETE', `/admin/regulatory/openfda/codes/${q(code)}`),
    regSync: () => json('POST', '/admin/regulatory/openfda/sync'),
    regEcfr: () => json('GET', '/admin/regulatory/ecfr'),
    regAddPart: (part, label) => json('POST', '/admin/regulatory/ecfr/parts', { body: { part, label: label || null } }),
    regRemovePart: (part) => json('DELETE', `/admin/regulatory/ecfr/parts/${q(part)}`),
    regEcfrSync: () => json('POST', '/admin/regulatory/ecfr/sync'),
    regEcfrPending: () => json('GET', '/admin/regulatory/ecfr/pending').then((r) => r.updates),
    regPending: () => json('GET', '/admin/regulatory/openfda/pending').then((r) => r.updates),
    regPendingDetail: (id) => json('GET', `/admin/regulatory/openfda/pending/${id}`),
    regApprove: (id) => json('POST', `/admin/regulatory/openfda/pending/${id}/approve`),
    regReject: (id) => json('POST', `/admin/regulatory/openfda/pending/${id}/reject`),
    // UK legislation.gov.uk ('uk') aur EU EUR-Lex ('eu'): approve/reject/detail wohi regApprove/regReject/regPendingDetail
    regLaw: (src) => json('GET', `/admin/regulatory/law/${src}`),
    regLawAdd: (src, code, label) => json('POST', `/admin/regulatory/law/${src}/items`, { body: { code, label: label || null } }),
    regLawRemove: (src, code) => json('DELETE', `/admin/regulatory/law/${src}/items/${String(code).split('/').map(q).join('/')}`),
    regLawSync: (src) => json('POST', `/admin/regulatory/law/${src}/sync`),
    regLawPending: (src) => json('GET', `/admin/regulatory/law/${src}/pending`).then((r) => r.updates),

    // company admin: sirf apni company
    companyGet: () => json('GET', '/company'),
    companyCreateUser: (body) => json('POST', '/company/users', { body }),
    companyUpdateUser: (username, body) => json('PATCH', `/company/users/${q(username)}`, { body }),
  };
}
