# Company AI Server: API contract (frontend ke liye)

Base: `/api/v1`. JSON. Auth: `Authorization: Bearer <session token>` (login se). Har response mein `X-Request-ID`.
Errors: `{"detail": "message"}` (422 mein `detail` list hoti hai). Frontend rules:
**401 → login page** · **429 → `Retry-After` seconds ruk kar retry/toast** · 403 → "access nahi" · 502/504 → "model busy/down, dobara try karo".

## Auth
| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/auth/login` (public) | `{username, password}` | `{token, token_type:"bearer", expires_at (epoch sec), user:{username, role, collections\|null}}` · 401 galat · 429 bohot attempts |
| GET | `/auth/me` | | `{username, role, collections}` |
| POST | `/auth/logout` | | `{status:"ok"}` |
| POST | `/auth/change-password` | `{current_password, new_password (8-128)}` | `{token, expires_at}` **naya token save karo** (purane sab session band) · 400 galat current |

## Conversations (har user ko sirf apni; dusre ki id par 404)
| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/conversations?limit=50&offset=0` | | `{conversations:[{id,title,created_at,updated_at}]}` (naya pehle) |
| POST | `/conversations` | `{title?}` ya `{}` | 201 `{id,title,created_at,updated_at}` |
| GET | `/conversations/{id}` | | `{id,title,..., messages:[{id,role,content,status,created_at,sources}]}` |
| PATCH | `/conversations/{id}` | `{title}` | `{id,title}` |
| DELETE | `/conversations/{id}` | | `{status:"deleted"}` |

`message.status`: `complete` · `stopped` (user ne stop kiya, adhura jawab) · `error`. `sources`: list ya `null`.
`id` = 32 hex chars. Title pehle message se khud ban jata hai (pehle "New chat").

## Chat (streaming, SSE) : `POST /conversations/{id}/chat`
Body (ya to `message` ya `regenerate`, dono nahi):
```json
{"message": "text (1-12000)", "regenerate": false, "collection": "hr", "temperature": 0.2, "max_tokens": 300}
```
- `regenerate: true` → aakhri assistant jawab hata kar dobara.
- `collection` set ho to company documents se jawab + sources (collection ki access na ho to 403).
- Response `text/event-stream`. **fetch + ReadableStream** use karo (EventSource POST/headers nahi support karta).
- **Stop button = `AbortController.abort()`**: server GPU free karta hai aur adhura jawab `stopped` save karta hai.

SSE events (har event `\n\n` se khatam):
```
event: sources                      <- sirf collection ke saath, sab se pehle
data: {"sources":[{"ref":1,"doc_id":"refund","chunk_index":0,"score":0.55,"preview":"..."}]}

data: {"choices":[{"delta":{"content":"He"}, ...}]}      <- har token chunk (default event)
data: {"choices":[{"delta":{"content":"llo"}, ...}]}
data: {"choices":[{"delta":{}, "finish_reason":"stop"}], "timings":{...}}
data: [DONE]                        <- aakhri

event: error                        <- generation beech mein fail hui
data: {"detail":"Streaming inference failed"}
```
Jawab ka text = saare `choices[0].delta.content` jodo (null/missing ignore). `[ref]` jaise `[1]` jawab ke andar sources se milte hain.
**HTTP status stream shuru hone se PEHLE aate hain**: 429 (queue full / aap ki pichli request abhi chal rahi: max 2 ek saath) · 504 queue timeout · 502 model down · 403 collection · 404 conversation.
Stream shuru hone ke baad error aaye to `event: error`.

## RAG (documents)
| Method | Path | Notes |
|---|---|---|
| GET | `/rag/collections` | `{collections:[{name,documents}]}` sirf wo jin tak access hai (dropdown) |
| POST | `/rag/ingest-file?filename=x.pdf&collection=hr&doc_id=?` | **admin**, raw body (`Content-Type` koi bhi). .txt .md .pdf .docx, ≤20MB. 413/415/422 detail mein wajah (scanned PDF → OCR chahiye) |
| POST | `/rag/ingest` | admin, `{doc_id,text,collection,metadata}` |
| DELETE | `/rag/documents/{doc_id}?collection=` | admin |
| GET | `/rag/stats` | admin |
| POST | `/rag/search` / `/rag/query` | retrieval / one-shot jawab (conversation chat ke bajaye ye UI mein zaroori nahi) |

## Admin (role=admin)
| Method | Path | Body |
|---|---|---|
| GET | `/admin/users` | → `{users:[{id,username,role,collections,disabled}]}` |
| POST | `/admin/users` | `{username (3-64: A-Za-z0-9_.@-), password (8-128), role:"user"\|"admin", collections:[..]\|null}` → 201 |
| PATCH | `/admin/users/{username}` | any of `{password, role, disabled, collections}` (`collections: null` = sab; field na bhejo = na badle) |

## Misc
`GET /health`, `GET /ready` (public) · `GET /usage/me?since_hours=` · `POST /chat/completions` (single-shot, non-stream).

## Security notes (frontend)
- Token ko `sessionStorage`/memory mein rakho; **LLM output markdown ko sanitize karke render karo** (XSS). CSP proxy se aata hai.
- 401 par token hatao aur login par bhejo. Logout par `/auth/logout` bulao.
- Production mein HTTPS zaroori (warna password/token LAN par plain-text).
