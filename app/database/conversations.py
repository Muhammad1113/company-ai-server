"""Conversation history (SQLite). Owner = ClientInfo.name (user ya API-key client)."""
import asyncio
import json
import os
import sqlite3
import threading
import time
import uuid

DEFAULT_TITLE = "New chat"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_owner ON conversations (owner, updated_at);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'complete',
    created_at REAL NOT NULL,
    sources TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages (conversation_id, id);
"""


def title_from(message: str, limit: int = 60) -> str:
    line = (message.strip().splitlines() or [""])[0].strip()
    return (line[:limit].rstrip() + "…") if len(line) > limit else (line or DEFAULT_TITLE)


def _conv(row) -> dict:
    return {"id": row[0], "title": row[2], "created_at": row[3], "updated_at": row[4]}


class ConversationRepository:

    def __init__(self, path: str):
        self.path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def _init_sync(self):
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        # Purani DB (phase6) mein sources column nahi tha: migrate
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(messages)")}
        if "sources" not in columns:
            self._conn.execute("ALTER TABLE messages ADD COLUMN sources TEXT")
        self._conn.commit()

    async def init(self):
        await asyncio.to_thread(self._init_sync)

    async def close(self):
        if self._conn is not None:
            conn, self._conn = self._conn, None
            await asyncio.to_thread(conn.close)

    def _run(self, sql, args=(), one=False, commit=False):
        with self._lock:
            cur = self._conn.execute(sql, args)
            data = cur.fetchone() if one else cur.fetchall()
            if commit:
                self._conn.commit()
            return data, cur.rowcount

    # ---------- conversations ----------
    def _create(self, owner, title):
        cid, now = uuid.uuid4().hex, time.time()
        self._run("INSERT INTO conversations VALUES (?,?,?,?,?)", (cid, owner, title, now, now), commit=True)
        return {"id": cid, "title": title, "created_at": now, "updated_at": now}

    async def create(self, owner: str, title: str = DEFAULT_TITLE) -> dict:
        return await asyncio.to_thread(self._create, owner, title or DEFAULT_TITLE)

    async def list_conversations(self, owner: str, limit: int = 50, offset: int = 0) -> list[dict]:
        rows, _ = await asyncio.to_thread(
            self._run, "SELECT * FROM conversations WHERE owner=? ORDER BY updated_at DESC, rowid DESC LIMIT ? OFFSET ?",
            (owner, limit, offset))
        return [_conv(r) for r in rows]

    async def get(self, conversation_id: str, owner: str) -> dict | None:
        row, _ = await asyncio.to_thread(
            self._run, "SELECT * FROM conversations WHERE id=? AND owner=?", (conversation_id, owner), True)
        return _conv(row) if row else None

    async def rename(self, conversation_id: str, owner: str, title: str) -> bool:
        _, n = await asyncio.to_thread(
            self._run, "UPDATE conversations SET title=? WHERE id=? AND owner=?",
            (title, conversation_id, owner), False, True)
        return n > 0

    def _delete(self, conversation_id, owner):
        _, n = self._run("DELETE FROM conversations WHERE id=? AND owner=?", (conversation_id, owner), commit=True)
        if n:
            self._run("DELETE FROM messages WHERE conversation_id=?", (conversation_id,), commit=True)
        return n > 0

    async def delete(self, conversation_id: str, owner: str) -> bool:
        return await asyncio.to_thread(self._delete, conversation_id, owner)

    async def set_title_if_default(self, conversation_id: str, title: str) -> None:
        await asyncio.to_thread(
            self._run, "UPDATE conversations SET title=? WHERE id=? AND title=?",
            (title, conversation_id, DEFAULT_TITLE), False, True)

    # ---------- messages (caller ownership pehle check kare) ----------
    def _add(self, conversation_id, role, content, status, sources=None):
        now = time.time()
        _, _ = self._run(
            "INSERT INTO messages (conversation_id, role, content, status, created_at, sources) "
            "VALUES (?,?,?,?,?,?)",
            (conversation_id, role, content, status, now, json.dumps(sources) if sources else None),
            commit=True)
        # MAX(..., purana + 1ms): Windows par clock ~15ms ke steps mein chalti hai; barabar timestamp par
        # "sab se naya" ka order ghalat na ho (har update purane updated_at se strictly aage jata hai)
        self._run("UPDATE conversations SET updated_at=MAX(?, updated_at + 0.001) WHERE id=?",
                  (now, conversation_id), commit=True)

    async def add_message(self, conversation_id: str, role: str, content: str,
                          status: str = "complete", sources: list[dict] | None = None) -> None:
        await asyncio.to_thread(self._add, conversation_id, role, content, status, sources)

    async def list_messages(self, conversation_id: str, limit: int | None = None) -> list[dict]:
        sql = ("SELECT id, role, content, status, created_at, sources FROM messages "
               "WHERE conversation_id=? ORDER BY id DESC")
        args: tuple = (conversation_id,)
        if limit:
            sql += " LIMIT ?"
            args += (limit,)
        rows, _ = await asyncio.to_thread(self._run, sql, args)
        return [{"id": r[0], "role": r[1], "content": r[2], "status": r[3], "created_at": r[4],
                 "sources": json.loads(r[5]) if r[5] else None}
                for r in reversed(rows)]

    def _delete_last_assistant(self, conversation_id):
        row, _ = self._run("SELECT id, role FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 1",
                           (conversation_id,), True)
        if row and row[1] == "assistant":
            self._run("DELETE FROM messages WHERE id=?", (row[0],), commit=True)
            return True
        return False

    async def delete_last_assistant(self, conversation_id: str) -> bool:
        return await asyncio.to_thread(self._delete_last_assistant, conversation_id)
