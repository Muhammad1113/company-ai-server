"""
Asli PostgreSQL + pgvector par test. Sirf tab chalta hai jab TEST_DATABASE_URL set ho:

  docker compose -f docker-compose.db.yml up -d
  $env:TEST_DATABASE_URL = "postgresql://company_ai:<password>@127.0.0.1:5432/company_ai"
  python -m pytest tests\\test_postgres_integration.py -v

Ye alag schema (test_company_ai) mein chalta hai, tumhare asli tables ko nahi chhuta.
"""
import asyncio
import os

import pytest

from app.database.postgres import PostgresDatabase, PostgresUsageRepository
from app.rag.pgvector_store import PgVectorStore

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)

DSN = os.environ.get("TEST_DATABASE_URL", "")
SCHEMA = "test_company_ai"


async def _fresh_db():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
    finally:
        await conn.close()
    db = PostgresDatabase(DSN, schema=SCHEMA)
    await db.connect()
    return db


def test_usage_repository_roundtrip():
    async def run():
        db = await _fresh_db()
        try:
            repo = PostgresUsageRepository(db)
            await repo.init()
            await repo.init()                                   # idempotent

            await repo.record(client="alice", endpoint="completions", status="success",
                              prompt_tokens=10, completion_tokens=20, latency_ms=100)
            await repo.record(client="alice", endpoint="completions", status="success",
                              prompt_tokens=5, completion_tokens=5, latency_ms=300, cache="hit")
            await repo.record(client="alice", endpoint="stream", status="cancelled")
            await repo.record(client="bob", endpoint="completions", status="success")

            s = await repo.summary("alice")
            assert s["requests"] == 3 and s["prompt_tokens"] == 15 and s["completion_tokens"] == 25
            assert s["avg_latency_ms"] == 200.0
            assert s["by_status"] == {"success": 2, "cancelled": 1}
            assert (await repo.summary("alice", since_hours=1))["requests"] == 3
            assert (await repo.summary("nobody"))["requests"] == 0
        finally:
            await db.close()
    asyncio.run(run())


def test_pgvector_store_roundtrip():
    async def run():
        db = await _fresh_db()
        try:
            store = PgVectorStore(db, "test-emb", dim=4)
            await store.init()
            await store.init()                                  # idempotent

            await store.replace_document("refund", "fin", [
                {"chunk_index": 0, "text": "refund 30 days", "vector": [1.0, 0.0, 0.0, 0.0], "metadata": {"src": "a"}}])
            await store.replace_document("leave", "fin", [
                {"chunk_index": 0, "text": "annual leave", "vector": [0.0, 1.0, 0.0, 0.0]}])
            await store.replace_document("other", "hr", [
                {"chunk_index": 0, "text": "vpn", "vector": [1.0, 0.0, 0.0, 0.0]}])

            hits = await store.search([0.9, 0.1, 0.0, 0.0], 5, "fin")
            assert [h["doc_id"] for h in hits] == ["refund", "leave"]        # collection filter + ranking
            assert hits[0]["score"] > hits[1]["score"] and hits[0]["metadata"] == {"src": "a"}

            await store.replace_document("refund", "fin", [                   # re-ingest => replace
                {"chunk_index": 0, "text": "new text", "vector": [1.0, 0.0, 0.0, 0.0]}])
            stats = await store.stats()
            assert stats["chunks"] == 3 and stats["collections"] == {"fin": 2, "hr": 1}

            assert await store.delete_document("refund", "fin") == 1
            assert await store.delete_document("refund", "fin") == 0
        finally:
            await db.close()
    asyncio.run(run())


def test_dimension_and_embedder_guards():
    async def run():
        db = await _fresh_db()
        try:
            store = PgVectorStore(db, "model-a", dim=4)
            await store.init()
            await store.replace_document("d", "c", [
                {"chunk_index": 0, "text": "t", "vector": [1.0, 0.0, 0.0, 0.0]}])

            for bad in (PgVectorStore(db, "model-a", dim=8), PgVectorStore(db, "model-b", dim=4)):
                try:
                    await bad.init()
                    assert False, "should have raised"
                except ValueError:
                    pass
        finally:
            await db.close()
    asyncio.run(run())
