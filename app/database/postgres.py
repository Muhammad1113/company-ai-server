"""
PostgreSQL helpers (asyncpg, lazy import: DATABASE_URL khali ho to asyncpg
install karne ki zaroorat nahi).
"""
import logging
import re
from contextlib import asynccontextmanager

logger = logging.getLogger("company_ai.postgres")

_SCHEMA_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DDL_LOCK_ID = 7272001   # advisory lock: multiple workers ek saath DDL na chalayen

_USAGE_DDL = """
CREATE TABLE IF NOT EXISTS usage_log (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id TEXT,
    client TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    status TEXT NOT NULL,
    cache TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    latency_ms DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_usage_client_ts ON usage_log (client, ts);
"""


class PostgresDatabase:

    def __init__(self, dsn: str, schema: str | None = None,
                 min_size: int = 1, max_size: int = 10):
        if schema is not None and not _SCHEMA_NAME.match(schema):
            raise ValueError("invalid schema name")
        self.dsn = dsn
        self.schema = schema
        self.min_size = min_size
        self.max_size = max_size
        self.pool = None

    async def connect(self):
        try:
            import asyncpg
        except ImportError as exc:
            raise RuntimeError(
                "DATABASE_URL is set but asyncpg is missing: pip install asyncpg"
            ) from exc

        kwargs = {}
        if self.schema:
            conn = await asyncpg.connect(self.dsn)
            try:
                await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
            finally:
                await conn.close()
            kwargs["server_settings"] = {"search_path": f'"{self.schema}",public'}

        self.pool = await asyncpg.create_pool(
            self.dsn,
            min_size=self.min_size,
            max_size=self.max_size,
            command_timeout=30,
            **kwargs,
        )
        logger.info("postgres pool ready")

    async def ping(self) -> bool:
        if self.pool is None:
            return False
        try:
            async with self.pool.acquire() as conn:
                return await conn.fetchval("SELECT 1") == 1
        except Exception:
            return False

    @asynccontextmanager
    async def ddl(self):
        """Transaction + advisory lock (idempotent DDL ke liye)."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock($1)", _DDL_LOCK_ID)
                yield conn

    async def close(self):
        if self.pool is not None:
            pool, self.pool = self.pool, None
            await pool.close()


class PostgresUsageRepository:
    """UsageRepository (SQLite) jaisa hi interface: init/record/summary/close."""

    def __init__(self, db: PostgresDatabase):
        self.db = db
        self._ready = False

    async def init(self) -> None:
        async with self.db.ddl() as conn:
            await conn.execute(_USAGE_DDL)
        self._ready = True

    async def record(
        self,
        *,
        client: str,
        endpoint: str,
        status: str,
        request_id: str | None = None,
        cache: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        latency_ms: float | None = None,
    ) -> None:
        """Best-effort: DB error kabhi request fail nahi karta."""
        if not self._ready:
            return
        try:
            await self.db.pool.execute(
                "INSERT INTO usage_log (request_id, client, endpoint, status, "
                "cache, prompt_tokens, completion_tokens, latency_ms) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                request_id, client, endpoint, status, cache,
                prompt_tokens, completion_tokens, latency_ms,
            )
        except Exception:
            logger.exception("usage record failed")

    async def summary(self, client: str, since_hours: float | None = None) -> dict:
        if not self._ready:
            return {"client": client, "requests": 0, "by_status": {}}

        where = (
            "client = $1 AND ($2::float8 IS NULL OR "
            "ts >= now() - ($2::float8 * interval '1 hour'))"
        )
        total = await self.db.pool.fetchrow(
            "SELECT COUNT(*) AS n, COALESCE(SUM(prompt_tokens),0) AS p, "
            "COALESCE(SUM(completion_tokens),0) AS c, AVG(latency_ms) AS avg_ms "
            f"FROM usage_log WHERE {where}",
            client, since_hours,
        )
        rows = await self.db.pool.fetch(
            f"SELECT status, COUNT(*) AS n FROM usage_log WHERE {where} GROUP BY status",
            client, since_hours,
        )
        return {
            "client": client,
            "requests": int(total["n"]),
            "prompt_tokens": int(total["p"]),
            "completion_tokens": int(total["c"]),
            "avg_latency_ms": (
                round(float(total["avg_ms"]), 1)
                if total["avg_ms"] is not None else None
            ),
            "by_status": {r["status"]: int(r["n"]) for r in rows},
        }

    async def close(self) -> None:
        pass   # pool PostgresDatabase.close() band karta hai
