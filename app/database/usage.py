"""
Usage log (SQLite, stdlib). Interface Postgres ke liye same rahega:
init / record / summary / close. Sab blocking kaam thread mein.
"""
import asyncio
import logging
import os
import sqlite3
import threading
import time

logger = logging.getLogger("company_ai.usage")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    request_id TEXT,
    client TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    status TEXT NOT NULL,
    cache TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    latency_ms REAL
);
CREATE INDEX IF NOT EXISTS idx_usage_client_ts ON usage_log (client, ts);
"""


class UsageRepository:

    def __init__(self, path: str):
        self.path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def _init_sync(self):
        if self.path != ":memory:":
            folder = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(folder, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    async def init(self) -> None:
        await asyncio.to_thread(self._init_sync)

    def _record_sync(self, row: tuple):
        with self._lock:
            self._conn.execute(
                "INSERT INTO usage_log (ts, request_id, client, endpoint, "
                "status, cache, prompt_tokens, completion_tokens, latency_ms) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                row,
            )
            self._conn.commit()

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
        if self._conn is None:
            return
        try:
            await asyncio.to_thread(
                self._record_sync,
                (time.time(), request_id, client, endpoint, status, cache,
                 prompt_tokens, completion_tokens, latency_ms),
            )
        except Exception:
            logger.exception("usage record failed")

    def _summary_sync(self, client: str, since_hours: float | None) -> dict:
        since = 0.0 if since_hours is None else time.time() - since_hours * 3600
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(prompt_tokens),0), "
                "COALESCE(SUM(completion_tokens),0), AVG(latency_ms) "
                "FROM usage_log WHERE client=? AND ts>=?",
                (client, since),
            ).fetchone()
            by_status = self._conn.execute(
                "SELECT status, COUNT(*) FROM usage_log "
                "WHERE client=? AND ts>=? GROUP BY status",
                (client, since),
            ).fetchall()

        return {
            "client": client,
            "requests": total[0],
            "prompt_tokens": total[1],
            "completion_tokens": total[2],
            "avg_latency_ms": round(total[3], 1) if total[3] is not None else None,
            "by_status": {status: count for status, count in by_status},
        }

    async def summary(self, client: str, since_hours: float | None = None) -> dict:
        if self._conn is None:
            return {"client": client, "requests": 0, "by_status": {}}
        return await asyncio.to_thread(self._summary_sync, client, since_hours)

    async def close(self) -> None:
        if self._conn is not None:
            conn, self._conn = self._conn, None
            await asyncio.to_thread(conn.close)
