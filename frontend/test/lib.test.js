import test from 'node:test';
import assert from 'node:assert/strict';
import { parseMarkdown, parseInline, toPlainText, safeUrl } from '../src/lib/markdown.js';
import { iterateSse, parseEvent } from '../src/lib/sse.js';
import { initialStream, reduceStream, normalizeEvent, dropLastAssistant } from '../src/lib/chatState.js';
import { createTokenStore } from '../src/lib/storage.js';
import { createApi, detailOf, ApiError } from '../src/lib/api.js';
import { groupByRecency, clamp, initials, percent } from '../src/lib/format.js';

const memStore = () => { const m = new Map(); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, v), removeItem: (k) => m.delete(k) }; };
const streamOf = (chunks) => new ReadableStream({ start(c) { chunks.forEach((x) => c.enqueue(typeof x === 'string' ? new TextEncoder().encode(x) : x)); c.close(); } });

// ---------- markdown ----------
test('raw HTML sirf text ban jata hai', () => {
  const ast = parseMarkdown('<script>alert(1)</script> <img src=x onerror=alert(1)>');
  assert.equal(ast[0].type, 'paragraph');
  assert.ok(toPlainText(ast[0].children).includes('<script>'));
  assert.ok(ast[0].children.every((n) => n.type === 'text'));
});

test('javascript: / data: links block, http/https/mailto theek', () => {
  assert.equal(safeUrl('javascript:alert(1)'), null);
  assert.equal(safeUrl('JaVaScRiPt:alert(1)'), null);
  assert.equal(safeUrl('data:text/html,x'), null);
  assert.equal(safeUrl('vbscript:x'), null);
  assert.equal(safeUrl('//evil.com'), null);
  assert.equal(safeUrl('https://a.com/x'), 'https://a.com/x');
  assert.equal(safeUrl('mailto:a@b.com'), 'mailto:a@b.com');
  const [l] = parseInline('[click](javascript:alert(1))');
  assert.equal(l.type, 'link');
  assert.equal(l.href, null);
});

test('heading, list, code fence, table, quote', () => {
  const src = '# Title\n\n- a\n- b\n\n1. x\n2. y\n\n```js\nconst a = 1;\n```\n\n| h1 | h2 |\n|---|:-:|\n| 1 | 2 |\n\n> quote';
  const t = parseMarkdown(src).map((n) => n.type);
  assert.deepEqual(t, ['heading', 'list', 'list', 'code', 'table', 'blockquote']);
  const code = parseMarkdown(src).find((n) => n.type === 'code');
  assert.equal(code.lang, 'js');
  assert.equal(code.text, 'const a = 1;');
  const table = parseMarkdown(src).find((n) => n.type === 'table');
  assert.deepEqual(table.align, [null, 'center']);
});

test('streaming: adhura code fence aur adhura ** crash nahi karte', () => {
  const a = parseMarkdown('Here:\n```python\nprint(1)');
  assert.equal(a[1].type, 'code');
  assert.equal(a[1].text, 'print(1)');
  const b = parseInline('hello **bold');
  assert.equal(toPlainText(b), 'hello **bold');
});

test('Urdu / Roman Urdu text salamat', () => {
  const ast = parseMarkdown('**ریفنڈ** پالیسی: 30 دن [1]');
  assert.equal(toPlainText(ast[0].children), 'ریفنڈ پالیسی: 30 دن [1]');
});

test('snake_case aur 2025. year paragraph na toote', () => {
  assert.equal(toPlainText(parseInline('my_var_name here')), 'my_var_name here');
  const ast = parseMarkdown('Text line\n2025. was a year');
  assert.equal(ast.length, 1);
});

// ---------- sse ----------
test('parseEvent: event name + multi-line data', () => {
  assert.deepEqual(parseEvent('event: sources\ndata: {"a":1}'), { event: 'sources', data: '{"a":1}' });
  assert.deepEqual(parseEvent('data: a\ndata: b'), { event: 'message', data: 'a\nb' });
  assert.equal(parseEvent(': comment'), null);
});

test('iterateSse: chunk beech mein toote, multi-byte Urdu safe', async () => {
  const text = 'data: {"t":"سلام"}\n\ndata: [DONE]\n\n';
  const bytes = new TextEncoder().encode(text);
  const chunks = [];
  for (let i = 0; i < bytes.length; i += 3) chunks.push(bytes.slice(i, i + 3));   // 3-byte tukde
  const out = [];
  for await (const ev of iterateSse(streamOf(chunks))) out.push(ev);
  assert.equal(out.length, 2);
  assert.equal(JSON.parse(out[0].data).t, 'سلام');
  assert.equal(out[1].data, '[DONE]');
});

test('iterateSse: CRLF aur aakhri event bina blank line', async () => {
  const out = [];
  for await (const ev of iterateSse(streamOf(['data: one\r\n\r\ndata: two']))) out.push(ev.data);
  assert.deepEqual(out, ['one', 'two']);
});

// ---------- chatState ----------
test('normalizeEvent + reduceStream: sources, tokens, done', () => {
  const evs = [
    normalizeEvent({ event: 'sources', data: JSON.stringify({ sources: [{ ref: 1, doc_id: 'refund', score: 0.5 }] }) }),
    normalizeEvent({ event: 'message', data: JSON.stringify({ choices: [{ delta: { content: 'He' } }] }) }),
    normalizeEvent({ event: 'message', data: JSON.stringify({ choices: [{ delta: { content: 'llo' } }] }) }),
    normalizeEvent({ event: 'message', data: JSON.stringify({ choices: [{ delta: {}, finish_reason: 'stop' }] }) }),
    normalizeEvent({ event: 'message', data: '[DONE]' }),
  ].filter(Boolean);
  const s = evs.reduce(reduceStream, initialStream());
  assert.equal(s.text, 'Hello');
  assert.equal(s.sources[0].doc_id, 'refund');
  assert.equal(s.status, 'complete');
});

test('error event, abort, aur done error ko overwrite nahi karta', () => {
  let s = reduceStream(initialStream(), normalizeEvent({ event: 'error', data: '{"detail":"boom"}' }));
  assert.equal(s.status, 'error');
  assert.equal(s.error, 'boom');
  s = reduceStream(s, { type: 'done' });
  assert.equal(s.status, 'error');
  assert.equal(reduceStream(initialStream(), { type: 'abort' }).status, 'stopped');
  assert.equal(reduceStream(reduceStream(initialStream(), { type: 'done' }), { type: 'abort' }).status, 'complete');
});

test('dropLastAssistant', () => {
  const m = [{ role: 'user' }, { role: 'assistant' }];
  assert.equal(dropLastAssistant(m).length, 1);
  assert.equal(dropLastAssistant([{ role: 'user' }]).length, 1);
});

// ---------- storage ----------
test('token store: remember => local, warna session; expiry; refresh', () => {
  let now = 1_000_000;
  const local = memStore(); const session = memStore();
  const s = createTokenStore(local, session, () => now);
  s.set('tok', 2000, { username: 'a' }, false);
  assert.equal(s.get(), 'tok');
  assert.equal(local.getItem('cai.auth'), null);
  assert.ok(session.getItem('cai.auth'));
  s.set('tok2', 2000, { username: 'a' }, true);
  assert.equal(session.getItem('cai.auth'), null);        // set() pehle clear karta hai
  assert.ok(local.getItem('cai.auth'));
  s.refresh('tok3', 3000);
  assert.equal(s.get(), 'tok3');
  assert.equal(s.session().user.username, 'a');
  now = 3_000_001;                                        // expire
  assert.equal(s.get(), null);
  assert.equal(local.getItem('cai.auth'), null);
});

test('token store: corrupt entry ignore', () => {
  const local = memStore(); const session = memStore();
  session.setItem('cai.auth', '{not json');
  const s = createTokenStore(local, session);
  assert.equal(s.get(), null);
});

// ---------- api ----------
const mkRes = (status, body, headers = {}) => ({
  ok: status >= 200 && status < 300, status,
  headers: { get: (k) => headers[k] ?? null },
  json: async () => body, body: null,
});

test('api: Authorization header, 401 => onUnauthorized, 429 Retry-After', async () => {
  const calls = [];
  let unauth = 0;
  const api = createApi({
    getToken: () => 'T',
    onUnauthorized: () => { unauth++; },
    fetchImpl: async (url, opts) => {
      calls.push([url, opts]);
      if (url.endsWith('/auth/me')) return mkRes(401, { detail: 'Invalid token' });
      if (url.endsWith('/conversations')) return mkRes(429, { detail: 'slow down' }, { 'Retry-After': '7' });
      return mkRes(200, { conversations: [{ id: 'x' }] });
    },
  });
  await assert.rejects(api.me(), (e) => e instanceof ApiError && e.status === 401);
  assert.equal(unauth, 1);
  assert.equal(calls[0][1].headers.Authorization, 'Bearer T');
  await assert.rejects(api.createConversation(), (e) => e.status === 429 && e.retryAfter === 7);
  assert.deepEqual(await api.listConversations().catch(() => 'err'), [{ id: 'x' }]);
});

test('api: login bina token ke, wrong password 401 par onUnauthorized nahi', async () => {
  let unauth = 0; let headers;
  const api = createApi({
    getToken: () => 'T', onUnauthorized: () => { unauth++; },
    fetchImpl: async (url, opts) => { headers = opts.headers; return mkRes(401, { detail: 'Invalid username or password' }); },
  });
  await assert.rejects(api.login('a', 'b'), (e) => e.detail === 'Invalid username or password');
  assert.equal(headers.Authorization, undefined);
  assert.equal(unauth, 0);
});

test('api: network error => ApiError(0)', async () => {
  const api = createApi({ fetchImpl: async () => { throw new TypeError('fetch failed'); } });
  await assert.rejects(api.me(), (e) => e.status === 0);
});

test('api: chatStream events deta hai', async () => {
  const sse = 'event: sources\ndata: {"sources":[{"ref":1}]}\n\ndata: {"choices":[{"delta":{"content":"Hi"}}]}\n\ndata: [DONE]\n\n';
  const api = createApi({ fetchImpl: async () => ({ ok: true, status: 200, headers: { get: () => null }, body: streamOf([sse]) }) });
  const got = [];
  await api.chatStream('id', { message: 'x' }, { onEvent: (e) => got.push(e.type) });
  assert.deepEqual(got, ['sources', 'token', 'done']);
});

test('detailOf: 422 list', () => {
  assert.equal(detailOf({ detail: [{ loc: ['body', 'message'], msg: 'too short' }] }, 'x'), 'message: too short');
  assert.equal(detailOf(null, 'fallback'), 'fallback');
});

// ---------- format ----------
test('groupByRecency', () => {
  const now = new Date(2026, 10, 7, 15, 0, 0).getTime();
  const sec = (d) => new Date(2026, 10, d, 12).getTime() / 1000;
  const groups = groupByRecency([
    { id: 1, updated_at: sec(7) }, { id: 2, updated_at: sec(6) }, { id: 3, updated_at: sec(3) }, { id: 4, updated_at: new Date(2026, 9, 25, 12).getTime() / 1000 },
  ], now);
  assert.deepEqual(groups.map((g) => [g.label, g.items.map((i) => i.id)]),
    [['Today', [1]], ['Yesterday', [2]], ['Previous 7 days', [3]], ['Older', [4]]]);
});

test('clamp, initials, percent', () => {
  assert.equal(clamp('abcdef', 4), 'abc…');
  assert.equal(initials('sh'), 'SH');
  assert.equal(percent(0.554), '55%');
  assert.equal(percent(2), '100%');
});

// ---------- sources ----------
import { planSources, isNotFound, groupByDoc } from '../src/lib/sources.js';

test('sources: not-found jawab par chhup jate hain', () => {
  const src = [{ ref: 1, doc_id: 'a.pdf', chunk_index: 0, score: 0.4 }];
  assert.equal(isNotFound('I could not find this in the documents.'), true);
  assert.equal(planSources(src, 'I could not find this in the documents.').hidden, true);
});

test('sources: sirf cited dikhte hain, baaqi more mein; doc ke hisaab se group', () => {
  const src = [1, 2, 3, 4].map((n) => ({ ref: n, doc_id: 'a.pdf', chunk_index: n, score: n / 10 }));
  const p = planSources(src, 'Lead time is 14 to 28 days [2].');
  assert.deepEqual(p.cited.map((s) => s.ref), [2]);
  assert.equal(p.more.length, 3);
  const g = groupByDoc(src);
  assert.equal(g.length, 1);
  assert.equal(g[0].items.length, 4);
  // Citation patch: documents mile magar jawab ne cite nahi kiye => sources panel chhupa rehta hai
  const none = planSources(src, 'No citations here');
  assert.equal(none.hidden, true);
  assert.equal(none.cited.length, 0);
  // Internet links cite na hon tab bhi dikhte hain
  const web = [{ ref: 1, kind: 'web', url: 'https://example.com/a', title: 'A' }];
  const w = planSources(web, 'No citations here');
  assert.equal(w.hidden, false);
  assert.equal(w.cited.length, 1);
});

test('sources: "I do not know" jawab par bhi chhup jate hain', () => {
  const src = [{ ref: 1, doc_id: 'a.pdf', chunk_index: 0, score: 0.4 }];
  assert.equal(planSources(src, 'I do not know. Please contact the sales team for specific information about AI.').hidden, true);
  assert.equal(planSources(src, "I don't know. Contact the sales team.").hidden, true);
  assert.equal(planSources(src, 'Lead time is 14 days [1].').hidden, false);
});

// ---------- quote / verify / export ----------
import { calcQuote } from '../src/lib/quote.js';
import { needsVerify } from '../src/lib/verify.js';
import { toMarkdown } from '../src/lib/exportChat.js';

test('quote: FOB, discount aur per piece sahi', () => {
  const q = calcQuote({ items: [{ price: 4.2, qty: 500 }], discountPct: 5, packing: 75, incoterm: 'FOB' });
  assert.equal(q.goods, 2100);
  assert.equal(q.discount, 105);
  assert.equal(q.total, 2070);
  assert.equal(q.perUnit, 4.14);
});

test('quote: float ki ghalti nahi (0.1 + 0.2 jaisi)', () => {
  const q = calcQuote({ items: [{ price: 0.1, qty: 3 }, { price: 0.2, qty: 3 }], incoterm: 'EXW' });
  assert.equal(q.total, 0.9);
});

test('quote: EXW mein freight/local nahi, CIF mein insurance 110%', () => {
  const base = { items: [{ price: 10, qty: 100 }], freight: 200, local: 50, insurancePct: 1 };
  assert.equal(calcQuote({ ...base, incoterm: 'EXW' }).total, 1000);
  assert.equal(calcQuote({ ...base, incoterm: 'FOB' }).total, 1050);
  assert.equal(calcQuote({ ...base, incoterm: 'CFR' }).total, 1250);
  const cif = calcQuote({ ...base, incoterm: 'CIF' });
  assert.equal(cif.insurance, 13.75);
  assert.equal(cif.total, 1263.75);
});

test('quote: ulta/khali input crash nahi karta, rate se convert', () => {
  const q = calcQuote({ items: [{ price: '', qty: -5 }, { price: 'x', qty: 2 }], discountPct: 500, incoterm: 'FOB', rate: 280 });
  assert.equal(q.total, 0);
  assert.equal(calcQuote({ items: [{ price: 2, qty: 10 }], rate: 280 }).converted, 5600);
});

test('verify: number wale jawab par note, refusal par nahi', () => {
  assert.equal(needsVerify('Lead time is 14 to 28 days.'), true);
  assert.equal(needsVerify('You need ISO 13485 and CE marking.'), true);
  assert.equal(needsVerify('I could not find this in the documents.'), false);
  assert.equal(needsVerify('Hello there'), false);
});

test('export: markdown mein sawal, jawab aur document ke naam', () => {
  const md = toMarkdown([
    { role: 'user', content: 'MOQ?' },
    { role: 'assistant', content: '100 pcs [1]', sources: [{ doc_id: 'a.pdf' }, { doc_id: 'a.pdf' }] },
  ], 'T');
  assert.ok(md.startsWith('# T'));
  assert.ok(md.includes('**Question:**') && md.includes('100 pcs [1]'));
  assert.ok(md.includes('_Sources: a.pdf_'));
});

// ---------- internet sources ----------
import { originOf, isWeb, domainOf } from '../src/lib/sources.js';

test('origin: docs / web / mixed / none', () => {
  const doc = { ref: 1, doc_id: 'a.pdf', chunk_index: 0, score: 0.5 };
  const web = { ref: 2, kind: 'web', url: 'https://en.wikipedia.org/wiki/Steel', title: 'Steel' };
  assert.equal(originOf([doc]), 'docs');
  assert.equal(originOf([web]), 'web');
  assert.equal(originOf([doc, web]), 'mixed');
  assert.equal(originOf([]), null);
  assert.equal(originOf(null), null);
  assert.equal(isWeb({ url: 'https://x.com' }), true);
  assert.equal(isWeb({ doc_id: 'a', kind: 'doc', url: 'https://x.com' }), false);
});

test('domainOf: www hata kar host, kharab url par khali', () => {
  assert.equal(domainOf('https://www.who.int/news'), 'who.int');
  assert.equal(domainOf('not a url'), '');
});

test('web sources: planSources unhe bhi sambhalta hai', () => {
  const web = [1, 2].map((n) => ({ ref: n, kind: 'web', url: `https://a.com/${n}`, title: `T${n}`, preview: 'x' }));
  const p = planSources(web, 'Steel is an alloy [1].');
  assert.deepEqual(p.cited.map((s) => s.ref), [1]);
  assert.equal(p.more.length, 1);
});
