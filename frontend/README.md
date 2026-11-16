# Frontend (React + Vite)

```
npm install
npm run dev        # http://localhost:5173, /api proxied to http://127.0.0.1:8000
npm test           # node --test (pure logic: SSE parser, markdown sanitizer, sources, quote calculator ...)
npm run build      # dist/ is served by Caddy (deploy/Dockerfile.web) or any static server
```
- Auth token: `sessionStorage` by default, `localStorage` when "remember me" is ticked.
- LLM markdown is rendered by `src/lib/markdown.js` (HTML escaped, unsafe link schemes blocked).
- Set `VITE_ALL_COLLECTIONS=1` at build time only if the backend supports `collection: "*"` (it does).
