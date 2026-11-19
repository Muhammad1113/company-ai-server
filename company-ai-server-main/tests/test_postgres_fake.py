"""
Postgres code ka Python-level logic, fake asyncpg pool ke saath (SQL execute nahi hota).
Asli SQL ke liye: tests/test_postgres_integration.py (TEST_DATABASE_URL chahiye).
"""
import asyncio
import json

from app.database.postgres import PostgresDatabase, PostgresUsageRepository
from app.rag.pgvector_store import PgVectorStore


class _Tx:
    def __init__(self, log): self.log = log
    async def __aenter__(self): self.log.append(("BEGIN",))
    async def __aexit__(self, *exc): self.log.append(("ROLLBACK" if exc[0] else "COMMIT",))


class FakeConn:
    def __init__(self, log, answers):
        self.log, self.answers = log, answers

    def _answer(self, sql, default=None):
        for key, value in self.answers.items():
            if key in sql:
                return value
        return default

    async def execute(self, sql, *args):
        self.log.append(("execute", sql, args))
        return self._answer(sql, "OK")

    async def executemany(self, sql, rows):
        self.log.append(("executemany", sql, list(rows)))

    async def fetch(self, sql, *args):
        self.log.append(("fetch", sql, args))
        return self._answer(sql, [])

    async def fetchrow(self, sql, *args):
        self.log.append(("fetchrow", sql, args))
        return self._answer(sql)

    async def fetchval(self, sql, *args):
        self.log.append(("fetchval", sql, args))
        return self._answer(sql)

    def transaction(self): return _Tx(self.log)


class _Acquire:
    def __init__(self, conn): self.conn = conn
    async def __aenter__(self): return self.conn
    async def __aexit__(self, *exc): pass


class FakePool(FakeConn):
    def acquire(self): return _Acquire(self)


def _db(answers=None):
    log = []
    db = PostgresDatabase("postgresql://fake")
    db.pool = FakePool(log, answers or {})
    return db, log


def _raises(coro_fn, exc):
    try:
        asyncio.run(coro_fn())
    except exc as e:
        return str(e)
    raise AssertionError("expected " + exc.__name__)


def test_replace_document_transaction_and_vector_format():
    db, log = _db()
    store = PgVectorStore(db, "emb-x", dim=3)
    items = [
        {"chunk_index": 0, "text": "alpha", "vector": [1.0, 0.0, 0.5], "metadata": {"a": "b"}},
        {"chunk_index": 1, "text": "beta", "vector": [0.0, 1.0, 0.25]},
    ]
    count = asyncio.run(store.replace_document("doc1", "hr", items))

    assert count == 2
    kinds = [entry[0] for entry in log]
    assert kinds == ["BEGIN", "execute", "executemany", "COMMIT"]      # delete -> insert, ek transaction

    _, delete_sql, delete_args = log[1]
    assert delete_sql.startswith("DELETE FROM rag_chunks") and delete_args == ("doc1", "hr")

    _, insert_sql, rows = log[2]
    assert "$7::text::vector" in insert_sql and "$5::text::jsonb" in insert_sql
    assert rows[0] == ("hr", "doc1", 0, "alpha", json.dumps({"a": "b"}), "emb-x", "[1.000000,0.000000,0.500000]")
    assert rows[1][4] == "{}" and rows[1][6] == "[0.000000,1.000000,0.250000]"


def test_dimension_mismatch_rejected_before_any_sql():
    db, log = _db()
    store = PgVectorStore(db, "emb-x", dim=4)

    msg = _raises(lambda: store.replace_document("d", "c", [{"chunk_index": 0, "text": "t", "vector": [1.0, 2.0]}]), ValueError)
    assert "2 dimensions" in msg and "RAG_EMBED_DIM=4" in msg
    _raises(lambda: store.search([1.0], 3, "c"), ValueError)
    assert log == []


def test_search_parses_rows():
    rows = [
        {"doc_id": "refund", "chunk_index": 0, "text": "30 days", "metadata": '{"k": "v"}', "score": 0.91234},
        {"doc_id": "leave", "chunk_index": 2, "text": "20 days", "metadata": None, "score": 0.5},
    ]
    db, log = _db({"FROM rag_chunks WHERE collection": rows})
    store = PgVectorStore(db, "emb-x", dim=2)

    hits = asyncio.run(store.search([0.6, 0.8], 2, "finance"))

    assert [h["doc_id"] for h in hits] == ["refund", "leave"]
    assert hits[0]["score"] == 0.9123 and hits[0]["metadata"] == {"k": "v"}
    assert hits[1]["metadata"] == {} and hits[0]["collection"] == "finance"
    _, sql, args = log[0]
    assert args == ("[0.600000,0.800000]", "finance", 2)
    assert "ORDER BY embedding <=> $1::text::vector LIMIT $3" in sql


def test_delete_parses_status_and_stats():
    db, _ = _db({"DELETE FROM rag_chunks": "DELETE 3",
                 "GROUP BY collection": [
                     {"collection": "hr", "docs": 2, "chunks": 5},
                     {"collection": "fin", "docs": 1, "chunks": 1}]})
    store = PgVectorStore(db, "emb-x", dim=2)

    assert asyncio.run(store.delete_document("d", "hr")) == 3
    assert asyncio.run(store.stats()) == {
        "embedder": "emb-x", "chunks": 6, "collections": {"hr": 2, "fin": 1}}


def test_init_detects_dimension_and_embedder_mismatch():
    db, _ = _db({"atttypmod": 1024})
    store = PgVectorStore(db, "emb-x", dim=512)
    # init() db.ddl() use karta hai: fake pool par context manager chahiye
    assert "vector(1024)" in _init_error(store, db)

    db2, _ = _db({"atttypmod": 512, "SELECT embedder": "old-model"})
    msg = _init_error(PgVectorStore(db2, "emb-x", dim=512), db2)
    assert "old-model" in msg and "re-ingest" in msg


def _init_error(store, db):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def ddl():
        async with db.pool.acquire() as conn:
            async with conn.transaction():
                yield conn

    db.ddl = ddl
    return _raises(store.init, ValueError)


def test_init_ok_creates_table_with_dim_and_optional_hnsw():
    db, log = _db({"atttypmod": 8})
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def ddl():
        async with db.pool.acquire() as conn:
            async with conn.transaction():
                yield conn

    db.ddl = ddl
    asyncio.run(PgVectorStore(db, "emb-x", dim=8, hnsw=True).init())
    statements = " ".join(entry[1] for entry in log if entry[0] == "execute")
    assert "CREATE EXTENSION IF NOT EXISTS vector" in statements
    assert "vector(8)" in statements and "USING hnsw" in statements


def test_usage_repo_record_and_summary():
    db, log = _db({
        "SELECT COUNT(*)": {"n": 3, "p": 15, "c": 25, "avg_ms": 200.04},
        "GROUP BY status": [{"status": "success", "n": 2}, {"status": "cancelled", "n": 1}],
    })
    repo = PostgresUsageRepository(db)

    asyncio.run(repo.record(client="x", endpoint="e", status="s"))   # init se pehle: no-op
    assert log == []

    repo._ready = True
    asyncio.run(repo.record(client="alice", endpoint="completions", status="success",
                            request_id="r1", cache="hit", prompt_tokens=3,
                            completion_tokens=1, latency_ms=12.5))
    kind, sql, args = log[0]
    assert kind == "execute" and "INSERT INTO usage_log" in sql
    assert args == ("r1", "alice", "completions", "success", "hit", 3, 1, 12.5)

    summary = asyncio.run(repo.summary("alice", since_hours=24))
    assert summary == {"client": "alice", "requests": 3, "prompt_tokens": 15,
                       "completion_tokens": 25, "avg_latency_ms": 200.0,
                       "by_status": {"success": 2, "cancelled": 1}}


def test_usage_record_failure_is_swallowed():
    class Boom(FakePool):
        async def execute(self, sql, *args): raise ConnectionError("db down")

    db = PostgresDatabase("postgresql://fake")
    db.pool = Boom([], {})
    repo = PostgresUsageRepository(db)
    repo._ready = True
    asyncio.run(repo.record(client="a", endpoint="e", status="s"))   # raise nahi karna


def test_invalid_schema_name_rejected():
    try:
        PostgresDatabase("postgresql://x", schema='bad"; DROP TABLE x;--')
        assert False
    except ValueError:
        pass
