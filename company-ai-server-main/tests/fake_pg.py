"""
asyncpg ki nakal (SQLite par) sirf Postgres repositories ke PYTHON logic ko bina Postgres ke chalane ke liye.
Ye PostgreSQL ka SQL dialect sabit NAHI karta: woh tests/test_repo_parity.py + test_pg_isolation_http.py asli
PostgreSQL par karte hain (scripts/setup_postgres.ps1).
"""
import json
import re
import sqlite3
from contextlib import asynccontextmanager


class UniqueViolationError(Exception):
    sqlstate = "23505"


def _tr(sql: str) -> str:
    sql = re.sub(r"\bBIGSERIAL PRIMARY KEY\b", "INTEGER PRIMARY KEY AUTOINCREMENT", sql)
    sql = re.sub(r"\bseq BIGSERIAL\b", "seq INTEGER", sql)
    sql = re.sub(r"\bBIGINT\b", "INTEGER", sql)
    sql = re.sub(r"\bDOUBLE PRECISION\b", "REAL", sql)
    sql = re.sub(r"\$(\d+)::text\[\]", r"$\1", sql)
    sql = re.sub(r"=\s*ANY\((\$\d+)\)", r" IN (SELECT value FROM json_each(\1))", sql)
    sql = re.sub(r"::float8|::text", "", sql)
    sql = re.sub(r"\bGREATEST\(", "max(", sql)
    sql = re.sub(r"\s+FOR UPDATE( OF \w+)?", "", sql)
    return re.sub(r"\$(\d+)", r"?\1", sql)


def _args(args):
    return tuple(json.dumps(a) if isinstance(a, list) else a for a in args)


class _Conn:
    def __init__(self, raw):
        self.raw = raw

    def _run(self, sql, args):
        try:
            return self.raw.execute(_tr(sql), _args(args))
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc) or "PRIMARY" in str(exc):
                raise UniqueViolationError(str(exc))
            raise

    async def execute(self, sql, *args):
        if not args and sql.count(";") > 1:
            self.raw.executescript(_tr(sql))
            return "OK"
        cur = self._run(sql, args)
        verb = sql.strip().split()[0].upper()
        return f"INSERT 0 {cur.rowcount}" if verb == "INSERT" else f"{verb} {cur.rowcount}"

    async def executemany(self, sql, rows):
        for r in rows:
            self._run(sql, r)

    async def fetch(self, sql, *args):
        return self._run(sql, args).fetchall()

    async def fetchrow(self, sql, *args):
        return self._run(sql, args).fetchone()

    async def fetchval(self, sql, *args):
        row = self._run(sql, args).fetchone()
        return row[0] if row else None

    @asynccontextmanager
    async def transaction(self):
        self.raw.execute("BEGIN")
        try:
            yield
        except BaseException:
            self.raw.execute("ROLLBACK")
            raise
        self.raw.execute("COMMIT")


class FakePool(_Conn):
    @asynccontextmanager
    async def acquire(self):
        yield self


class FakeDb:
    def __init__(self):
        raw = sqlite3.connect(":memory:", isolation_level=None)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys=ON")
        self.pool = FakePool(raw)

    @asynccontextmanager
    async def ddl(self):
        yield self.pool
