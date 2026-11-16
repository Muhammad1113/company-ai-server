"""Audit log on PostgreSQL. Interface SQLite wale `AuditRepository` jaisi hi hai."""
import time

from app.database.audit import clean_detail, like_prefix, row_to_event

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    ts DOUBLE PRECISION NOT NULL,
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


class PgAuditRepository:

    def __init__(self, db):
        self.db = db
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    async def init(self):
        async with self.db.ddl() as conn:
            await conn.execute(_SCHEMA)
        self._ready = True

    async def close(self):
        self._ready = False

    async def record(self, actor, role, company, action, target=None, detail=None, ip=None, ok=True) -> None:
        await self.db.pool.execute(
            "INSERT INTO audit_log (ts, actor, role, company, action, target, detail, ip, ok) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            time.time(), actor, role, company, action, (target or None) and str(target)[:200],
            clean_detail(detail), ip, 1 if ok else 0)

    async def list(self, limit: int = 50, offset: int = 0, actor: str | None = None, action: str | None = None,
                   company: str | None = None, ok: bool | None = None) -> list[dict]:
        conds, args = [], []

        def add(sql_with_n, *values):
            n = len(args) + 1
            conds.append(sql_with_n.replace("{n}", f"${n}").replace("{m}", f"${n + 1}"))
            args.extend(values)

        if actor:
            add("lower(actor) = lower({n})", actor)
        if action:
            add("(action = {n} OR action LIKE {m} ESCAPE '\\')", action, like_prefix(action))
        if company:
            add("company = {n}", company)
        if ok is not None:
            add("ok = {n}", 1 if ok else 0)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        args += [limit, offset]
        rows = await self.db.pool.fetch(
            "SELECT id, ts, actor, role, company, action, target, detail, ip, ok FROM audit_log "
            f"{where} ORDER BY id DESC LIMIT ${len(args) - 1} OFFSET ${len(args)}", *args)
        return [row_to_event(tuple(r)) for r in rows]

    async def purge(self, older_than_days: float) -> int:
        status = await self.db.pool.execute("DELETE FROM audit_log WHERE ts < $1", time.time() - older_than_days * 86400)
        return int(status.split()[-1])
