import asyncio
import os
import sqlite3
import tempfile
from contextlib import closing

from app.database.conversations import ConversationRepository


def test_sources_roundtrip():
    async def run():
        repo = ConversationRepository(":memory:")
        await repo.init()
        c = await repo.create("alice")
        sources = [{"ref": 1, "doc_id": "refund", "chunk_index": 0, "score": 0.9, "preview": "30 days"}]

        await repo.add_message(c["id"], "user", "q")
        await repo.add_message(c["id"], "assistant", "a [1]", sources=sources)
        await repo.add_message(c["id"], "assistant", "plain")

        msgs = await repo.list_messages(c["id"])
        assert msgs[0]["sources"] is None and msgs[1]["sources"] == sources and msgs[2]["sources"] is None
    asyncio.run(run())


def test_old_database_is_migrated():
    async def run():
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = os.path.join(tmp, "old.db")
            with closing(sqlite3.connect(path)) as old:               # phase6 schema (sources nahi)
                old.executescript(
                    "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,"
                    " role TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'complete',"
                    " created_at REAL NOT NULL);"
                    "INSERT INTO messages (conversation_id, role, content, created_at) VALUES ('c1','user','old message',1.0);")
                old.commit()

            repo = ConversationRepository(path)
            await repo.init()
            assert [m["content"] for m in await repo.list_messages("c1")] == ["old message"]
            await repo.add_message("c1", "assistant", "new", sources=[{"ref": 1}])
            assert (await repo.list_messages("c1"))[-1]["sources"] == [{"ref": 1}]

            await repo.init()                                         # dobara init: migration idempotent
            await repo.close()
    asyncio.run(run())
