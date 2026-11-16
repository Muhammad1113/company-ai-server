r"""
SQLite (data/company_ai.db) -> PostgreSQL: users, companies, sessions, conversations, messages, regulatory tables.
Server band karke chalao. Dobara chalana safe hai (ON CONFLICT DO NOTHING: jo pehle se hai usse chhua nahi jata).

  python scripts\migrate_sqlite_to_postgres.py --dry-run          (sirf ginti dikhata hai)
  python scripts\migrate_sqlite_to_postgres.py                    (DATABASE_URL aur DATABASE_PATH .env se)
  python scripts\migrate_sqlite_to_postgres.py --sqlite data\company_ai.db --dsn postgresql://...

Usage log (usage_log) migrate nahi hota: sirf statistics hain, naya Postgres log khali se shuru hota hai.
Aakhir mein har table ki ginti SQLite aur PostgreSQL mein milai jati hai; farq ho to exit code 1.
"""
import argparse
import asyncio
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.database.pg_accounts import PgAccountsRepository  # noqa: E402
from app.database.pg_conversations import PgConversationRepository  # noqa: E402
from app.database.pg_regulatory import PgRegulatoryRepository  # noqa: E402
from app.database.postgres import PostgresDatabase  # noqa: E402

# (table, conflict key, sequence column or None). Order = foreign key order.
TABLES = [
    ("companies", "id", "id"),
    ("users", "id", "id"),
    ("sessions", "token_hash", None),
    ("conversations", "id", None),
    ("messages", "id", "id"),
    ("reg_versions", "id", "id"),
    ("openfda_codes", "code", None),
    ("openfda_snapshot", "key", None),
    ("openfda_ignored", "key, hash", None),
    ("openfda_pending", "id", "id"),
    ("openfda_state", "k", None),
    ("ecfr_parts", "part", None),
]
BATCH = 500


def _sqlite_tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _sqlite_cols(conn, table) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


async def _pg_cols(pool, table) -> list[str]:
    rows = await pool.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = $1 ORDER BY ordinal_position", table)
    return [r[0] for r in rows]


async def migrate(sqlite_path: str, dsn: str, dry_run: bool) -> int:
    if not Path(sqlite_path).is_file():
        print(f"SQLite file nahi mili: {sqlite_path}")
        return 1
    src = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    have = _sqlite_tables(src)

    if dry_run:
        print(f"[dry-run] {sqlite_path}")
        for table, _, _ in TABLES:
            if table in have:
                print(f"  {table:<18} {src.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]} rows")
        return 0

    db = PostgresDatabase(dsn)
    await db.connect()
    problems = 0
    try:
        await PgAccountsRepository(db).init()           # tables banao (idempotent)
        await PgConversationRepository(db).init()
        await PgRegulatoryRepository(db).init()
        now = time.time()
        for table, conflict, seq in TABLES:
            if table not in have:
                print(f"  {table:<18} SQLite mein nahi (skip)")
                continue
            pg_cols = set(await _pg_cols(db.pool, table))
            cols = [c for c in _sqlite_cols(src, table) if c in pg_cols]
            where = " WHERE expires_at > ?" if table == "sessions" else ""     # expired sessions nahi
            args = (now,) if table == "sessions" else ()
            cur = src.execute(f"SELECT {', '.join(cols)} FROM {table}{where} ORDER BY rowid", args)
            placeholders = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
            sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
                   f"ON CONFLICT ({conflict}) DO NOTHING")
            copied = 0
            while True:
                batch = cur.fetchmany(BATCH)
                if not batch:
                    break
                async with db.pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.executemany(sql, [tuple(r) for r in batch])
                copied += len(batch)
            if seq:                                    # BIGSERIAL ko max(id) ke baad se chalao
                await db.pool.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}', '{seq}'), "
                    f"GREATEST((SELECT COALESCE(MAX({seq}), 0) FROM {table}), 1), "
                    f"(SELECT COUNT(*) > 0 FROM {table}))")
            n_src = src.execute(f"SELECT COUNT(*) FROM {table}{where}", args).fetchone()[0]
            n_pg = await db.pool.fetchval(f"SELECT COUNT(*) FROM {table}")
            ok = n_pg >= n_src
            problems += 0 if ok else 1
            print(f"  {table:<18} sqlite={n_src:<6} postgres={n_pg:<6} {'OK' if ok else 'MISMATCH'}")
    finally:
        await db.close()
        src.close()
    print("DONE: sab ginti barabar." if not problems else f"{problems} table(s) mein farq: upar dekho.")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default=settings.DATABASE_PATH)
    ap.add_argument("--dsn", default=settings.DATABASE_URL)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.dry_run and not args.dsn:
        print("DATABASE_URL (.env) ya --dsn zaroori hai.")
        return 1
    return asyncio.run(migrate(args.sqlite, args.dsn, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
