# Internet search + Auto mode: backend plan

## Status
| Part | Status |
|---|---|
| `app/services/websearch.py` (DuckDuckGo via ddgs, Wikipedia fallback, query cleaning, cache) | Written, 7 unit tests pass (mocked network). NOT tested live: my sandbox has no internet |
| Frontend: Auto mode, origin badge, website source cards, page number display, admin tick-boxes | Done in frontend.zip, 37 tests pass |
| Chat route: auto routing (docs first, internet second) | NOT done. Needs your route code |
| Page numbers in sources | NOT done. Needs your PDF extractor and chunker |

## Step 1: check that search works on your network (2 minutes)
```powershell
.\.venv\Scripts\activate
pip install ddgs
python -m app.services.websearch "what is AISI 420 stainless steel"
```
You should see JSON with `"provider": "duckduckgo"` and 4 results. If DuckDuckGo is blocked or rate limited you will see `"provider": "wikipedia"` (fallback). If `results` is empty, tell me the `error` text.

## Step 2: how the chat route should decide (design)
Score alone is not enough. In your own screenshots a real company question scored about 55 percent and "what is ai" scored 46 percent, too close for one threshold. So:

1. Retrieve from every collection this user may see (admin: all; user: `collections` list; null: all). This is what `collection: "*"` means.
2. Docs-first prompt. Add to CHAT_SYSTEM_PROMPT:
   `Answer ONLY from the numbered document passages. Cite each fact like [1]. If the passages do not answer the question, reply with exactly NOT_IN_DOCUMENTS and nothing else.`
3. Server peeks at the first few tokens (your stream core already has prefetch):
   - Not `NOT_IN_DOCUMENTS` -> stream as normal, send `event: sources` with document sources.
   - `NOT_IN_DOCUMENTS` and web search enabled -> `WebSearch().search(user_question)`, then run the model again with `WEB_RULES` + `build_web_context(results)`, send `event: sources` with `to_sources(results)`.
   - `NOT_IN_DOCUMENTS` and no web results -> save and show: `I could not find this in the documents or on the internet.`
4. Save the web sources in the message `sources` column like document sources (frontend already renders `kind: "web"`).
5. Setting `WEB_SEARCH_ENABLED=0` by default; turn on in `.env`. Reason: the question text leaves the company network. `sanitize_query` strips emails, urls and long numbers, but a question can still contain private words. Tell your staff.

## Step 3: page numbers
Sources can carry `"page": 3`; the frontend shows "page 3" instead of "part 4" when present. For that, the PDF extractor must return text per page and the chunker must remember the page where each chunk starts. Send me `app/rag/extractors.py` and `app/rag/chunker.py` and I will add it. Documents already uploaded must be re-uploaded once to get pages.

## Source JSON the frontend understands
```json
{"ref": 1, "doc_id": "price_list_SAMPLE.pdf", "chunk_index": 2, "page": 1, "score": 0.61, "preview": "..."}
{"ref": 2, "kind": "web", "doc_id": "en.wikipedia.org", "title": "Stainless steel", "url": "https://en.wikipedia.org/wiki/Stainless_steel", "preview": "...", "score": null}
```

## Per-user document access
Already in the backend: when the admin creates or edits a user, `collections` decides what that user may search (null = all). The Admin screen now shows the collections as tick boxes. Access is per collection, not per single file. To give two users different files, put those files in different collections (for example `catalog`, `finance`).

## Files I need to finish the route
`app/api/routes/conversations.py`, `app/services/messages.py`, `app/rag/service.py`, `app/api/streaming.py`, `app/core/config.py`, `app/rag/extractors.py`, `app/rag/chunker.py`. Upload them in the next message.

## Network problems (what your first run showed)
Run this first on the server machine:
```powershell
python -m app.services.websearch --diagnose
```
It tests every search engine and tells you which ones this machine can reach, and prints the `WEB_SEARCH_BACKENDS=` line to put in `.env`.
- `DNS ... Query Refused`: the DNS server of your network refuses that site. Change the DNS of the network adapter to 1.1.1.1 and 8.8.8.8 (Settings > Network > adapter > DNS), then run `--diagnose` again.
- Wikipedia `403`: Wikimedia blocks clients with no contact in the User-Agent. Set `WEB_SEARCH_CONTACT=your-admin-email@yourcompany.com` in `.env`.
