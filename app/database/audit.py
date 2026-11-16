"""
Admin / security audit log (SQLite, stdlib). Interface PostgreSQL wale `PgAuditRepository` jaisi hi hai.

Kya record hota hai: KAUN (actor, role, company), KYA (action), KIS PAR (target), kab, kahan se (ip), kamyab ya nahi.
Kya kabhi record nahi hota: passwords, sawal-jawab ya document ka text. `detail` mein sirf chhote safe fields jate hain.
"""
import asyncio
import json
import logging
import os
import sqlite3
import threading
import time

logger = logging.getLogger("company_ai.audit")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    actor TEXT NOT NULL,
    role TEXT,
    company TEXT,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT,
    ip TEXT,
    ok INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log (ts);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log (actor, ts);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log (action, ts);
"""
_MAX_DETAIL = 2000


def clean_detail(detail) -> str | None:
    if not detail:
        return None
    return json.dumps(detail, default=str, ensure_ascii=False, sort_keys=True)[:_MAX_DETAIL]


def like_prefix(action: str) -> str:
    """'user' / 'user.' => user.% (LIKE ke special characters escape)."""
    a = action.rstrip(".").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return a + ".%"


def row_to_event(r) -> dict:
    return {"id": r[0], "ts": r[1], "actor": r[2], "role": r[3], "company": r[4], "action": r[5],
            "target": r[6], "detail": json.loads(r[7]) if r[7] else None, "ip": r[8], "ok": bool(r[9])}


class AuditRepository:

    def __init__(self, path: str):
        self.path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self._conn is not None

    async def init(self):
        def _open():
            if self.path != ":memory:":
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.executescript(_SCHEMA)
            conn.commit()
            return conn
        self._conn = await asyncio.to_thread(_open)

    async def close(self):
        if self._conn is not None:
            conn, self._conn = self._conn, None
            await asyncio.to_thread(conn.close)

    def _exec(self, sql, args=(), *, commit=False, fetch=False):
        with self._lock:
            cur = self._conn.execute(sql, args)
            rows = cur.fetchall() if fetch else None
            n = cur.rowcount
            if commit:
                self._conn.commit()
            return rows if fetch else n

    async def record(self, actor, role, company, action, target=None, detail=None, ip=None, ok=True) -> None:
        await asyncio.to_thread(
            self._exec,
            "INSERT INTO audit_log (ts, actor, role, company, action, target, detail, ip, ok) VALUES (?,?,?,?,?,?,?,?,?)",
            (time.time(), actor, role, company, action, (target or None) and str(target)[:200],
             clean_detail(detail), ip, 1 if ok else 0), commit=True)

    async def list(self, limit: int = 50, offset: int = 0, actor: str | None = None, action: str | None = None,
                   company: str | None = None, ok: bool | None = None) -> list[dict]:
        conds, args = [], []
        if actor:
            conds.append("lower(actor) = lower(?)"); args.append(actor)
        if action:
            conds.append("(action = ? OR action LIKE ? ESCAPE '\\')"); args += [action, like_prefix(action)]
        if company:
            conds.append("company = ?"); args.append(company)
        if ok is not None:
            conds.append("ok = ?"); args.append(1 if ok else 0)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = await asyncio.to_thread(
            self._exec,
            "SELECT id, ts, actor, role, company, action, target, detail, ip, ok FROM audit_log "
            f"{where} ORDER BY id DESC LIMIT ? OFFSET ?", (*args, limit, offset), fetch=True)
        return [row_to_event(r) for r in rows]

    async def purge(self, older_than_days: float) -> int:
        cutoff = time.time() - older_than_days * 86400
        return await asyncio.to_thread(self._exec, "DELETE FROM audit_log WHERE ts < ?", (cutoff,), commit=True)
