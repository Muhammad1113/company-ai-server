
## Setup & Deployment Notes (Pilot Release)
- **PDF Page Numbers:** Page numbers are stored at document upload time. Re-upload PDFs on a fresh server so citations display Page N instead of Part N (openFDA and eCFR synced records always show Part N).
- **System Prompt Rules:** If using a custom CHAT_SYSTEM_PROMPT in .env or data/system_prompt.txt, include: Never add a "Note:" line about where your answer comes from, and never mention "the provided context" or "the documents provided".
- **Database & Vector Store:** Set RAG_STORE_BACKEND=pgvector and DATABASE_URL in .env when switching from local JSON/SQLite to Docker PostgreSQL + pgvector.
