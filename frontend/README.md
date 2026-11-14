# MetaXpert AI: Frontend (React + Vite)

Contract: `../docs/API.md`. Plain CSS (Tailwind nahi), runtime dependency sirf `react` + `react-dom`.

## Pehli baar
```powershell
cd frontend
npm install
npm test            # lib ke 37 tests (node:test, koi extra package nahi)
npm run build       # => frontend\dist
```

## Development (hot reload)
Backend `uvicorn` :8000 par chalta rahe, phir:
```powershell
npm run dev         # http://localhost:5173  (/api => 127.0.0.1:8000 proxy)
```

## Production
`npm run build` ke baad `frontend\dist` ko Caddy/Nginx serve karta hai
(`deploy\Caddyfile` / `deploy\nginx.conf` mein root path pehle se `...\frontend\dist` hai).
CSP `script-src 'self'` ke mutabiq: koi inline script, CDN ya external font nahi.
Har rebuild ke baad browser mein Ctrl+F5.

## Folder
```
src/lib/        api, sse, chatState, markdown, storage, format, clipboard, services (singletons)
src/context/    Auth (login/logout/expiry/change-password), Toast
src/hooks/      useChat (streaming, stop, regenerate, server se reconcile)
src/components/ Login, Sidebar, ChatView, Message, Markdown, Sources, Composer, Admin, dialogs
```

## Behaviour (mukhtasir)
- 401 kahin se bhi => token hata kar login page. Token expiry par khud logout.
- "Keep me signed in" off => sessionStorage (tab band = logout), on => localStorage.
- Har message/title/input `dir="auto"`: Urdu RTL, English LTR, khud detect.
- Stop = AbortController; adhura jawab "Stopped" note ke saath server save karta hai.
- Collection chunna => jawab ke saath `[1]` citation chips. Sources panel sirf wahi passages dikhata hai jo jawab mein cite hue, document ke hisaab se group; baaqi "other retrieved passages" ke peeche. "I could not find..." jawab par sources chhup jate hain.
- 429 => Retry-After ke saath message; message composer mein wapas aa jata hai.
- Admin (role=admin): users banao/edit/disable, documents upload/delete.

## Surgical / export ke features
- Quick chips (MOQ, Lead time, Certificates, Packing, Incoterms, Shipping docs, Care) jab collection chuni ho.
- "Draft buyer email" (aakhri jawab ke neeche): sirf usi jawab ke facts se email likhwata hai.
- Number/certificate wale jawab par peela "source se milao" note.
- Source par "Show full passage"; sirf cited sources dikhte hain.
- Sidebar mein Calculator: EXW/FCA/FOB/CFR/CIF quotation, discount, insurance, currency rate. Hisaab code se hota hai, model se nahi (lib/quote.js).
- Chat ke upar PDF (print) aur .md download.
- Thumbs up/down sirf browser mein save hota hai (localStorage), server ko nahi jata.

## Login ki background image
`public/login-bg.jpg` (space + globe) is project ke liye banai gayi hai. Apni image lagani ho to isi naam se
`public/` mein replace karo (1920x1080 ya bari, JPG, 300 KB tak behtar). Logo: `public/logo.jpg`, cover: `public/cover.jpg`.

## Optional settings (`frontend/.env`)
```
VITE_LOGIN_MOTION=0        # login page ka background ruk jata hai (purane PC ke liye). Default: chalta hai
VITE_MAX_TOKENS=1200       # lambe jawab (quotation/spec tables). Default 700
VITE_ALL_COLLECTIONS=1     # Auto mode (documents, phir internet), SIRF jab backend collection:"*" support kare. Backend plan: ../backend_addon/INTEGRATION.md
```
Background animation sirf user ke apne browser mein chalti hai (server ya server ke GPU par koi load nahi).
Dheema PC ho to scene khud dots aur frame rate kam kar leta hai. "Reduce motion" on ho to ruka rehta hai.

## Zabaan / persona
Frontend jawab ki zabaan nahi badalta. Rules backend ke `CHAT_SYSTEM_PROMPT` (`.env`) mein hain, dekho `chat_system_prompt.env.txt`.
