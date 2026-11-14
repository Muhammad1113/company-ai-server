"""
Regulatory data (SQLite, stdlib): versioned documents (EU/UK/AU/...) aur openFDA ka approval flow.

- reg_versions     : har regulatory document ki har version (active = index mein; archived/retired = sirf archive)
- openfda_codes    : jin FDA product codes ko track karna hai
- openfda_snapshot : aakhri APPROVED records (hash ke saath) -> naye/badle records inhi se pehchane jate hain
- openfda_pending  : approval ka intezar karte updates (super admin dekh kar approve/reject karta hai)
- openfda_ignored  : reject kiye hue (key, hash) -> dobara propose nahi hote
- ecfr_parts       : eCFR (21 CFR) ke jin parts ko track karna hai (snapshot/pending/ignored openFDA ke saath shared; pending.source se alag)
"""
import asyncio
import json
import os
import sqlite3
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reg_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    uploaded_at REAL NOT NULL,
    UNIQUE (collection, doc_key, version)
);
CREATE TABLE IF NOT EXISTS openfda_codes (
    code TEXT PRIMARY KEY, label TEXT, added_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS openfda_snapshot (
    key TEXT PRIMARY KEY, kind TEXT NOT NULL, product_code TEXT NOT NULL,
    hash TEXT NOT NULL, data TEXT NOT NULL, approved_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS openfda_ignored (
    key TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY (key, hash)
);
CREATE TABLE IF NOT EXISTS openfda_pending (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    payload TEXT NOT NULL,
    decided_by TEXT,
    decided_at REAL
);
CREATE TABLE IF NOT EXISTS openfda_state (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS ecfr_parts (
    part TEXT PRIMARY KEY, label TEXT, added_at REAL NOT NULL
);
"""

_VERSION_COLS = ("id", "collection", "doc_key", "version", "effective_date", "source_url", "note",
                 "status", "filename", "archive_dir", "chars", "chunks", "uploaded_by", "uploaded_at")


def _version(row) -> dict:
    return dict(zip(_VERSION_COLS, row))


class RegulatoryRepository:

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
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(openfda_pending)")}
        if "source" not in cols:                       # purane database: pehle sirf openFDA tha
            self._conn.execute("ALTER TABLE openfda_pending ADD COLUMN source TEXT NOT NULL DEFAULT 'openfda'")
        self._conn.commit()

    async def init(self):
        await asyncio.to_thread(self._init_sync)

    async def close(self):
        if self._conn is not None:
            conn, self._conn = self._conn, None
            await asyncio.to_thread(conn.close)

    def _q(self, sql, args=(), *, one=False, commit=False):
        with self._lock:
            cur = self._conn.execute(sql, args)
            result = cur.fetchone() if one else cur.fetchall()
            if commit:
                self._conn.commit()
            return result

    async def _run(self, *a, **k):
        return await asyncio.to_thread(self._q, *a, **k)

    # ---------- versioned documents ----------
    async def add_version(self, **v) -> int:
        def go():
            with self._lock:
                cur = self._conn.execute(
                    "INSERT INTO reg_versions (collection, doc_key, version, effective_date, source_url, note, "
                    "status, filename, archive_dir, chars, chunks, uploaded_by, uploaded_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (v["collection"], v["doc_key"], v["version"], v.get("effective_date"), v.get("source_url"),
                     v.get("note"), v["status"], v.get("filename"), v["archive_dir"], v.get("chars", 0),
                     v.get("chunks", 0), v.get("uploaded_by"), time.time()))
                self._conn.commit()
                return cur.lastrowid
        try:
            return await asyncio.to_thread(go)
        except sqlite3.IntegrityError:
            raise ValueError("this version already exists for this document; use a new version label")

    async def get_by_id(self, version_id: int) -> dict | None:
        row = await self._run("SELECT " + ",".join(_VERSION_COLS) + " FROM reg_versions WHERE id=?",
                              (version_id,), one=True)
        return _version(row) if row else None

    async def get_version(self, collection, doc_key, version) -> dict | None:
        row = await self._run("SELECT " + ",".join(_VERSION_COLS) + " FROM reg_versions "
                              "WHERE collection=? AND doc_key=? AND version=?", (collection, doc_key, version), one=True)
        return _version(row) if row else None

    async def get_active(self, collection, doc_key) -> dict | None:
        row = await self._run("SELECT " + ",".join(_VERSION_COLS) + " FROM reg_versions "
                              "WHERE collection=? AND doc_key=? AND status='active'", (collection, doc_key), one=True)
        return _version(row) if row else None

    async def set_status(self, version_id: int, status: str):
        await self._run("UPDATE reg_versions SET status=? WHERE id=?", (status, version_id), commit=True)

    async def list_versions(self, collection: str | None = None) -> list[dict]:
        sql = "SELECT " + ",".join(_VERSION_COLS) + " FROM reg_versions"
        args = ()
        if collection:
            sql += " WHERE collection=?"
            args = (collection,)
        rows = await self._run(sql + " ORDER BY collection, doc_key, uploaded_at DESC, id DESC", args)
        return [_version(r) for r in rows]

    # ---------- openFDA codes ----------
    async def list_codes(self) -> list[dict]:
        rows = await self._run("SELECT code, label, added_at FROM openfda_codes ORDER BY code")
        return [{"code": r[0], "label": r[1], "added_at": r[2]} for r in rows]

    async def add_code(self, code: str, label: str | None):
        await self._run("INSERT OR REPLACE INTO openfda_codes (code, label, added_at) VALUES (?,?,?)",
                        (code, label, time.time()), commit=True)

    async def remove_code(self, code: str) -> bool:
        def go():
            with self._lock:
                cur = self._conn.execute("DELETE FROM openfda_codes WHERE code=?", (code,))
                self._conn.commit()
                return cur.rowcount
        return bool(await asyncio.to_thread(go))

    # ---------- eCFR parts ----------
    async def list_parts(self) -> list[dict]:
        rows = await self._run("SELECT part, label, added_at FROM ecfr_parts ORDER BY CAST(part AS INTEGER)")
        return [{"part": r[0], "label": r[1], "added_at": r[2]} for r in rows]

    async def add_part(self, part: str, label: str | None):
        await self._run("INSERT OR REPLACE INTO ecfr_parts (part, label, added_at) VALUES (?,?,?)",
                        (part, label, time.time()), commit=True)

    async def remove_part(self, part: str) -> bool:
        def go():
            with self._lock:
                cur = self._conn.execute("DELETE FROM ecfr_parts WHERE part=?", (part,))
                self._conn.commit()
                return cur.rowcount
        return bool(await asyncio.to_thread(go))

    # ---------- snapshot / ignored ----------
    async def snapshot(self) -> dict[str, dict]:
        rows = await self._run("SELECT key, kind, product_code, hash, data FROM openfda_snapshot")
        return {r[0]: {"key": r[0], "kind": r[1], "code": r[2], "hash": r[3], "data": json.loads(r[4])} for r in rows}

    async def upsert_snapshot(self, records: list[dict]):
        def go():
            with self._lock:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO openfda_snapshot (key, kind, product_code, hash, data, approved_at) "
                    "VALUES (?,?,?,?,?,?)",
                    [(r["key"], r["kind"], r["code"], r["hash"], json.dumps(r["data"]), time.time()) for r in records])
                self._conn.commit()
        await asyncio.to_thread(go)

    async def delete_snapshot(self, keys: list[str]):
        def go():
            with self._lock:
                self._conn.executemany("DELETE FROM openfda_snapshot WHERE key=?", [(k,) for k in keys])
                self._conn.commit()
        await asyncio.to_thread(go)

    async def ignored(self) -> set[tuple[str, str]]:
        rows = await self._run("SELECT key, hash FROM openfda_ignored")
        return {(r[0], r[1]) for r in rows}

    async def add_ignored(self, pairs: list[tuple[str, str]]):
        def go():
            with self._lock:
                self._conn.executemany("INSERT OR IGNORE INTO openfda_ignored (key, hash) VALUES (?,?)", pairs)
                self._conn.commit()
        await asyncio.to_thread(go)

    # ---------- pending approvals ----------
    async def create_pending(self, summary: dict, payload: list[dict], source: str = "openfda") -> int:
        def go():
            with self._lock:
                self._conn.execute("UPDATE openfda_pending SET status='superseded' WHERE status='pending' AND source=?",
                                   (source,))
                cur = self._conn.execute(
                    "INSERT INTO openfda_pending (created_at, status, summary, payload, source) VALUES (?,?,?,?,?)",
                    (time.time(), "pending", json.dumps(summary), json.dumps(payload), source))
                self._conn.commit()
                return cur.lastrowid
        return await asyncio.to_thread(go)

    async def list_pending(self, include_decided: bool = False, limit: int = 20, source: str | None = None) -> list[dict]:
        conds, args = [], []
        if not include_decided:
            conds.append("status='pending'")
        if source:
            conds.append("source=?")
            args.append(source)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = await self._run("SELECT id, created_at, status, summary, decided_by, decided_at, source FROM openfda_pending "
                               f"{where} ORDER BY id DESC LIMIT ?", (*args, limit))
        return [{"id": r[0], "created_at": r[1], "status": r[2], "summary": json.loads(r[3]),
                 "decided_by": r[4], "decided_at": r[5], "source": r[6]} for r in rows]

    async def get_pending(self, pending_id: int) -> dict | None:
        r = await self._run("SELECT id, created_at, status, summary, payload, decided_by, decided_at, source "
                            "FROM openfda_pending WHERE id=?", (pending_id,), one=True)
        if not r:
            return None
        return {"id": r[0], "created_at": r[1], "status": r[2], "summary": json.loads(r[3]),
                "payload": json.loads(r[4]), "decided_by": r[5], "decided_at": r[6], "source": r[7]}

    async def decide(self, pending_id: int, status: str, by: str) -> bool:
        """Sirf 'pending' wala hi decide ho sakta hai (double-approve nahi)."""
        def go():
            with self._lock:
                cur = self._conn.execute(
                    "UPDATE openfda_pending SET status=?, decided_by=?, decided_at=? WHERE id=? AND status='pending'",
                    (status, by, time.time(), pending_id))
                self._conn.commit()
                return cur.rowcount
        return bool(await asyncio.to_thread(go))

    async def count_pending(self) -> int:
        if self._conn is None:          # init se pehle (jaise lifespan ke baghair tests) overview na toote
            return 0
        row = await self._run("SELECT COUNT(*) FROM openfda_pending WHERE status='pending'", one=True)
        return row[0]

    # ---------- misc state ----------
    async def set_state(self, key: str, value):
        await self._run("INSERT OR REPLACE INTO openfda_state (k, v) VALUES (?,?)", (key, json.dumps(value)), commit=True)

    async def get_state(self, key: str, default=None):
        row = await self._run("SELECT v FROM openfda_state WHERE k=?", (key,), one=True)
        return json.loads(row[0]) if row else default
