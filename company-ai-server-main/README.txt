MATURITY PATCH - file-by-file overwrite (same paths as your projects)

backend/  -> copy over backend project root (app/..., tests/...)
frontend/ -> copy over frontend project root (src/..., test/...)

After copying:
1. Restart backend (uvicorn).
2. RE-UPLOAD your PDFs once (page numbers are stored at ingest time; old documents still work but show "Part N" instead of "Page N").
   openFDA/eCFR synced documents have no pages (they are not PDFs) - they show "Part N".
3. If your .env has CHAT_SYSTEM_PROMPT or data/system_prompt.txt, the new rules in app/core/prompt.py are NOT used.
   Add this line to that prompt:
   - Never add a "Note:" line about where your answer comes from, and never mention "the provided context" or "the documents provided". The app already shows the source.
   (The RAG answer prompt in app/rag/service.py is in code, so it always applies.)
4. Frontend: npm run build / npm run dev. Tests: node --test test/answer.test.js ; backend: pytest tests/test_maturity.py
