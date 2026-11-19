"""Conversation history on PostgreSQL. Interface SQLite wale `ConversationRepository` jaisi hi hai."""
import json
import time
import uuid

from app.database.conversations import DEFAULT_TITLE, _conv

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    seq BIGSERIAL
);
CREATE INDEX IF NOT EXISTS idx_conv_owner ON conversations (owner, updated_at);
CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'complete',
    created_at DOUBLE PRECISION NOT NULL,
    sources TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages (conversation_id, id);
"""
_CONV_COLS = "id, owner, title, created_at, updated_at"


def _count(status: str) -> int:
    return int(status.split()[-1])


class PgConversationRepository:

    def __init__(self, db):
        self.db = db

    async def init(self):
        async with self.db.ddl() as conn:
            await conn.execute(_SCHEMA)

    async def close(self):
        pass

    async def create(self, owner: str, title: str = DEFAULT_TITLE) -> dict:
        title = title or DEFAULT_TITLE
        cid, now = uuid.uuid4().hex, time.time()
        await self.db.pool.execute(
            "INSERT INTO conversations (id, owner, title, created_at, updated_at) VALUES ($1,$2,$3,$4,$5)",
            cid, owner, title, now, now)
        return {"id": cid, "title": title, "created_at": now, "updated_at": now}

    async def list_conversations(self, owner: str, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = await self.db.pool.fetch(
            f"SELECT {_CONV_COLS} FROM conversations WHERE owner=$1 ORDER BY updated_at DESC, seq DESC "
            "LIMIT $2 OFFSET $3", owner, limit, offset)
        return [_conv(tuple(r)) for r in rows]

    async def get(self, conversation_id: str, owner: str) -> dict | None:
        row = await self.db.pool.fetchrow(
            f"SELECT {_CONV_COLS} FROM conversations WHERE id=$1 AND owner=$2", conversation_id, owner)
        return _conv(tuple(row)) if row else None

    async def rename(self, conversation_id: str, owner: str, title: str) -> bool:
        status = await self.db.pool.execute(
            "UPDATE conversations SET title=$1 WHERE id=$2 AND owner=$3", title, conversation_id, owner)
        return _count(status) > 0

    async def delete(self, conversation_id: str, owner: str) -> bool:
        status = await self.db.pool.execute(      # messages ON DELETE CASCADE
            "DELETE FROM conversations WHERE id=$1 AND owner=$2", conversation_id, owner)
        return _count(status) > 0

    async def set_title_if_default(self, conversation_id: str, title: str) -> None:
        await self.db.pool.execute(
            "UPDATE conversations SET title=$1 WHERE id=$2 AND title=$3", title, conversation_id, DEFAULT_TITLE)

    async def add_message(self, conversation_id: str, role: str, content: str,
                          status: str = "complete", sources: list[dict] | None = None) -> None:
        now = time.time()
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO messages (conversation_id, role, content, status, created_at, sources) "
                    "VALUES ($1,$2,$3,$4,$5,$6)",
                    conversation_id, role, content, status, now, json.dumps(sources) if sources else None)
                # har update purane updated_at se strictly aage (barabar timestamp par order na bigray)
                await conn.execute(
                    "UPDATE conversations SET updated_at=GREATEST($1::float8, updated_at + 0.001) WHERE id=$2",
                    now, conversation_id)

    async def list_messages(self, conversation_id: str, limit: int | None = None) -> list[dict]:
        sql = ("SELECT id, role, content, status, created_at, sources FROM messages "
               "WHERE conversation_id=$1 ORDER BY id DESC")
        args: list = [conversation_id]
        if limit:
            sql += " LIMIT $2"
            args.append(limit)
        rows = await self.db.pool.fetch(sql, *args)
        return [{"id": r[0], "role": r[1], "content": r[2], "status": r[3], "created_at": r[4],
                 "sources": json.loads(r[5]) if r[5] else None} for r in reversed(rows)]

    async def delete_last_assistant(self, conversation_id: str) -> bool:
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT id, role FROM messages WHERE conversation_id=$1 ORDER BY id DESC LIMIT 1 FOR UPDATE",
                    conversation_id)
                if row and row["role"] == "assistant":
                    await conn.execute("DELETE FROM messages WHERE id=$1", row["id"])
                    return True
        return False
