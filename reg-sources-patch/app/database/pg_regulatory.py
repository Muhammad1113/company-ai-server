"""Regulatory data on PostgreSQL. Interface SQLite wale `RegulatoryRepository` jaisi hi hai."""
import json
import time

from app.database.regulatory import _VERSION_COLS, _version

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reg_versions (
    id BIGSERIAL PRIMARY KEY,
    collection TEXT NOT NULL,
    doc_key TEXT NOT NULL,
    version TEXT NOT NULL,
    effective_date TEXT,
    source_url TEXT,
    note TEXT,
    status TEXT NOT NULL,
    filename TEXT,
    archive_dir TEXT NOT NULL,
    chars INTEGER NOT NULL DEFAULT 0,
    chunks INTEGER NOT NULL DEFAULT 0,
    uploaded_by TEXT,
    uploaded_at DOUBLE PRECISION NOT NULL,
    UNIQUE (collection, doc_key, version)
);
CREATE TABLE IF NOT EXISTS openfda_codes (
    code TEXT PRIMARY KEY, label TEXT, added_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS openfda_snapshot (
    key TEXT PRIMARY KEY, kind TEXT NOT NULL, product_code TEXT NOT NULL,
    hash TEXT NOT NULL, data TEXT NOT NULL, approved_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS openfda_ignored (
    key TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY (key, hash)
);
CREATE TABLE IF NOT EXISTS openfda_pending (
    id BIGSERIAL PRIMARY KEY,
    created_at DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    payload TEXT NOT NULL,
    decided_by TEXT,
    decided_at DOUBLE PRECISION,
    source TEXT NOT NULL DEFAULT 'openfda'
);
CREATE TABLE IF NOT EXISTS openfda_state (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS ecfr_parts (
    part TEXT PRIMARY KEY, label TEXT, added_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS reg_tracked (
    source TEXT NOT NULL, code TEXT NOT NULL, label TEXT, added_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (source, code)
);
"""
_VCOLS = ",".join(_VERSION_COLS)


def _count(status: str) -> int:
    return int(status.split()[-1])


class PgRegulatoryRepository:

    def __init__(self, db):
        self.db = db
        self._ready = False

    async def init(self):
        async with self.db.ddl() as conn:
            await conn.execute(_SCHEMA)
        self._ready = True

    async def close(self):
        self._ready = False

    # ---------- versioned documents ----------
    async def add_version(self, **v) -> int:
        try:
            return await self.db.pool.fetchval(
                "INSERT INTO reg_versions (collection, doc_key, version, effective_date, source_url, note, "
                "status, filename, archive_dir, chars, chunks, uploaded_by, uploaded_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) RETURNING id",
                v["collection"], v["doc_key"], v["version"], v.get("effective_date"), v.get("source_url"),
                v.get("note"), v["status"], v.get("filename"), v["archive_dir"], v.get("chars", 0),
                v.get("chunks", 0), v.get("uploaded_by"), time.time())
        except Exception as exc:
            if getattr(exc, "sqlstate", None) == "23505":
                raise ValueError("this version already exists for this document; use a new version label")
            raise

    async def get_by_id(self, version_id: int) -> dict | None:
        row = await self.db.pool.fetchrow(f"SELECT {_VCOLS} FROM reg_versions WHERE id=$1", version_id)
        return _version(tuple(row)) if row else None

    async def get_version(self, collection, doc_key, version) -> dict | None:
        row = await self.db.pool.fetchrow(
            f"SELECT {_VCOLS} FROM reg_versions WHERE collection=$1 AND doc_key=$2 AND version=$3",
            collection, doc_key, version)
        return _version(tuple(row)) if row else None

    async def get_active(self, collection, doc_key) -> dict | None:
        row = await self.db.pool.fetchrow(
            f"SELECT {_VCOLS} FROM reg_versions WHERE collection=$1 AND doc_key=$2 AND status='active'",
            collection, doc_key)
        return _version(tuple(row)) if row else None

    async def set_status(self, version_id: int, status: str):
        await self.db.pool.execute("UPDATE reg_versions SET status=$1 WHERE id=$2", status, version_id)

    async def list_versions(self, collection: str | None = None) -> list[dict]:
        order = " ORDER BY collection, doc_key, uploaded_at DESC, id DESC"
        if collection:
            rows = await self.db.pool.fetch(
                f"SELECT {_VCOLS} FROM reg_versions WHERE collection=$1" + order, collection)
        else:
            rows = await self.db.pool.fetch(f"SELECT {_VCOLS} FROM reg_versions" + order)
        return [_version(tuple(r)) for r in rows]

    # ---------- openFDA codes ----------
    async def list_codes(self) -> list[dict]:
        rows = await self.db.pool.fetch("SELECT code, label, added_at FROM openfda_codes ORDER BY code")
        return [{"code": r[0], "label": r[1], "added_at": r[2]} for r in rows]

    async def add_code(self, code: str, label: str | None):
        await self.db.pool.execute(
            "INSERT INTO openfda_codes (code, label, added_at) VALUES ($1,$2,$3) "
            "ON CONFLICT (code) DO UPDATE SET label=EXCLUDED.label, added_at=EXCLUDED.added_at",
            code, label, time.time())

    async def remove_code(self, code: str) -> bool:
        return _count(await self.db.pool.execute("DELETE FROM openfda_codes WHERE code=$1", code)) > 0

    # ---------- eCFR parts ----------
    async def list_parts(self) -> list[dict]:
        rows = await self.db.pool.fetch(
            "SELECT part, label, added_at FROM ecfr_parts ORDER BY CAST(part AS INTEGER)")
        return [{"part": r[0], "label": r[1], "added_at": r[2]} for r in rows]

    async def add_part(self, part: str, label: str | None):
        await self.db.pool.execute(
            "INSERT INTO ecfr_parts (part, label, added_at) VALUES ($1,$2,$3) "
            "ON CONFLICT (part) DO UPDATE SET label=EXCLUDED.label, added_at=EXCLUDED.added_at",
            part, label, time.time())

    async def remove_part(self, part: str) -> bool:
        return _count(await self.db.pool.execute("DELETE FROM ecfr_parts WHERE part=$1", part)) > 0

    # ---------- tracked items of law-text sources (uk / eu) ----------
    async def list_tracked(self, source: str) -> list[dict]:
        rows = await self.db.pool.fetch(
            "SELECT code, label, added_at FROM reg_tracked WHERE source=$1 ORDER BY code", source)
        return [{"code": r[0], "label": r[1], "added_at": r[2]} for r in rows]

    async def add_tracked(self, source: str, code: str, label: str | None):
        await self.db.pool.execute(
            "INSERT INTO reg_tracked (source, code, label, added_at) VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (source, code) DO UPDATE SET label=EXCLUDED.label, added_at=EXCLUDED.added_at",
            source, code, label, time.time())

    async def remove_tracked(self, source: str, code: str) -> bool:
        return _count(await self.db.pool.execute(
            "DELETE FROM reg_tracked WHERE source=$1 AND code=$2", source, code)) > 0

    # ---------- snapshot / ignored ----------
    async def snapshot(self) -> dict[str, dict]:
        rows = await self.db.pool.fetch("SELECT key, kind, product_code, hash, data FROM openfda_snapshot")
        return {r[0]: {"key": r[0], "kind": r[1], "code": r[2], "hash": r[3], "data": json.loads(r[4])}
                for r in rows}

    async def upsert_snapshot(self, records: list[dict]):
        if not records:
            return
        now = time.time()
        await self.db.pool.executemany(
            "INSERT INTO openfda_snapshot (key, kind, product_code, hash, data, approved_at) "
            "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (key) DO UPDATE SET kind=EXCLUDED.kind, "
            "product_code=EXCLUDED.product_code, hash=EXCLUDED.hash, data=EXCLUDED.data, "
            "approved_at=EXCLUDED.approved_at",
            [(r["key"], r["kind"], r["code"], r["hash"], json.dumps(r["data"]), now) for r in records])

    async def delete_snapshot(self, keys: list[str]):
        if keys:
            await self.db.pool.execute("DELETE FROM openfda_snapshot WHERE key = ANY($1::text[])", list(keys))

    async def ignored(self) -> set[tuple[str, str]]:
        rows = await self.db.pool.fetch("SELECT key, hash FROM openfda_ignored")
        return {(r[0], r[1]) for r in rows}

    async def add_ignored(self, pairs: list[tuple[str, str]]):
        if pairs:
            await self.db.pool.executemany(
                "INSERT INTO openfda_ignored (key, hash) VALUES ($1,$2) ON CONFLICT DO NOTHING",
                [tuple(p) for p in pairs])

    # ---------- pending approvals ----------
    async def create_pending(self, summary: dict, payload: list[dict], source: str = "openfda") -> int:
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE openfda_pending SET status='superseded' WHERE status='pending' AND source=$1", source)
                return await conn.fetchval(
                    "INSERT INTO openfda_pending (created_at, status, summary, payload, source) "
                    "VALUES ($1,'pending',$2,$3,$4) RETURNING id",
                    time.time(), json.dumps(summary), json.dumps(payload), source)

    async def list_pending(self, include_decided: bool = False, limit: int = 20,
                           source: str | None = None) -> list[dict]:
        conds, args = [], []
        if not include_decided:
            conds.append("status='pending'")
        if source:
            args.append(source)
            conds.append(f"source=${len(args)}")
        args.append(limit)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = await self.db.pool.fetch(
            "SELECT id, created_at, status, summary, decided_by, decided_at, source FROM openfda_pending "
            f"{where} ORDER BY id DESC LIMIT ${len(args)}", *args)
        return [{"id": r[0], "created_at": r[1], "status": r[2], "summary": json.loads(r[3]),
                 "decided_by": r[4], "decided_at": r[5], "source": r[6]} for r in rows]

    async def get_pending(self, pending_id: int) -> dict | None:
        r = await self.db.pool.fetchrow(
            "SELECT id, created_at, status, summary, payload, decided_by, decided_at, source "
            "FROM openfda_pending WHERE id=$1", pending_id)
        if not r:
            return None
        return {"id": r[0], "created_at": r[1], "status": r[2], "summary": json.loads(r[3]),
                "payload": json.loads(r[4]), "decided_by": r[5], "decided_at": r[6], "source": r[7]}

    async def decide(self, pending_id: int, status: str, by: str) -> bool:
        """Sirf 'pending' wala hi decide ho sakta hai (double-approve nahi)."""
        return _count(await self.db.pool.execute(
            "UPDATE openfda_pending SET status=$1, decided_by=$2, decided_at=$3 WHERE id=$4 AND status='pending'",
            status, by, time.time(), pending_id)) > 0

    async def count_pending(self) -> int:
        if not self._ready:
            return 0
        return int(await self.db.pool.fetchval("SELECT COUNT(*) FROM openfda_pending WHERE status='pending'"))

    # ---------- misc state ----------
    async def set_state(self, key: str, value):
        await self.db.pool.execute(
            "INSERT INTO openfda_state (k, v) VALUES ($1,$2) ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v",
            key, json.dumps(value))

    async def get_state(self, key: str, default=None):
        row = await self.db.pool.fetchrow("SELECT v FROM openfda_state WHERE k=$1", key)
        return json.loads(row[0]) if row else default
