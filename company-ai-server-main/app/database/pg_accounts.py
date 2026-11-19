"""
Accounts (companies, users, sessions) on PostgreSQL. Interface SQLite wale `AccountsRepository` jaisi hi hai
(container DATABASE_URL set hone par yahi use karta hai). Timestamps DOUBLE PRECISION epoch seconds (SQLite jaise).
"""
import asyncio
import logging
import re
import secrets
import time

from app.core import tenancy
from app.database.accounts import (
    MAX_PASSWORD, MIN_PASSWORD, ONLINE_WINDOW, ROLES, USERNAME_RE, _KEEP, _USER_SELECT, _company_public,
    _public, _token_hash, _validate_collections, hash_password, verify_password,
)

logger = logging.getLogger("company_ai.pg_accounts")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_COMPANY_COLS = "id, slug, name, email, disabled, max_users, created_at"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id BIGSERIAL PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    email TEXT,
    disabled INTEGER NOT NULL DEFAULT 0,
    max_users INTEGER NOT NULL DEFAULT 0,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    collections TEXT,
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at DOUBLE PRECISION NOT NULL,
    company_id BIGINT REFERENCES companies(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS users_username_ci ON users (lower(username));
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL,
    last_used DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);
"""


def _is_unique(exc) -> bool:
    return getattr(exc, "sqlstate", None) == "23505"


def _count(status: str) -> int:
    return int(status.split()[-1])


def _set_clause(pairs: list[tuple[str, object]]) -> tuple[str, list]:
    cols = [f"{c}=${i}" for i, (c, _) in enumerate(pairs, 1)]
    return ", ".join(cols), [v for _, v in pairs]


class PgAccountsRepository:

    def __init__(self, db, session_ttl_hours: float = 168.0, scrypt_n: int = 2 ** 14):
        self.db = db
        self.session_ttl = session_ttl_hours * 3600
        self.scrypt_n = scrypt_n
        self._dummy_hash = hash_password("dummy-password", n=scrypt_n)
        self.has_users = False
        self._ready = False

    # ---------- lifecycle ----------
    async def init(self):
        async with self.db.ddl() as conn:
            await conn.execute(_SCHEMA)
        self.has_users = (await self.db.pool.fetchval("SELECT COUNT(*) FROM users")) > 0
        await self.db.pool.execute("DELETE FROM sessions WHERE expires_at < $1", time.time())
        self._ready = True

    async def close(self):
        self._ready = False

    # ---------- companies ----------
    async def create_company(self, slug, name, email=None, max_users=0) -> dict:
        if not tenancy.valid_slug(slug):
            raise ValueError("company slug: 2-32 chars, a-z 0-9 and single underscores")
        name = (name or "").strip()
        if not name or len(name) > 120:
            raise ValueError("company name required (max 120 chars)")
        email = (email or "").strip() or None
        if email and not _EMAIL_RE.match(email):
            raise ValueError("invalid company email")
        if max_users < 0:
            raise ValueError("max_users must be >= 0 (0 = unlimited)")
        try:
            await self.db.pool.execute(
                "INSERT INTO companies (slug, name, email, max_users, created_at) VALUES ($1,$2,$3,$4,$5)",
                slug, name, email, max_users, time.time())
        except Exception as exc:
            if _is_unique(exc):
                raise ValueError("company already exists")
            raise
        return await self.get_company(slug)

    async def get_company(self, slug: str) -> dict | None:
        row = await self.db.pool.fetchrow(f"SELECT {_COMPANY_COLS} FROM companies WHERE slug=$1", slug)
        return _company_public(tuple(row)) if row else None

    async def list_companies(self) -> list[dict]:
        now = time.time()
        rows = await self.db.pool.fetch(
            f"SELECT c.id, c.slug, c.name, c.email, c.disabled, c.max_users, c.created_at, "
            " (SELECT COUNT(*) FROM users u WHERE u.company_id = c.id), "
            " (SELECT COUNT(DISTINCT s.user_id) FROM sessions s JOIN users u2 ON u2.id = s.user_id "
            "   WHERE u2.company_id = c.id AND s.expires_at > $1 AND s.last_used > $2) "
            "FROM companies c ORDER BY c.slug", now, now - ONLINE_WINDOW)
        out = []
        for r in rows:
            r = tuple(r)
            out.append({**_company_public(r), "users": int(r[7]), "online": int(r[8]),
                        "collection": tenancy.company_collection(r[1])})
        return out

    async def update_company(self, slug, *, name=None, email=_KEEP, disabled=None, max_users=None) -> bool:
        pairs: list[tuple[str, object]] = []
        if name is not None:
            if not name.strip() or len(name) > 120:
                raise ValueError("invalid company name")
            pairs.append(("name", name.strip()))
        if email is not _KEEP:
            email = (email or "").strip() or None
            if email and not _EMAIL_RE.match(email):
                raise ValueError("invalid company email")
            pairs.append(("email", email))
        if disabled is not None:
            pairs.append(("disabled", 1 if disabled else 0))
        if max_users is not None:
            if max_users < 0:
                raise ValueError("max_users must be >= 0")
            pairs.append(("max_users", max_users))
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                cid = await conn.fetchval("SELECT id FROM companies WHERE slug=$1", slug)
                if cid is None:
                    return False
                if pairs:
                    sets, args = _set_clause(pairs)
                    await conn.execute(f"UPDATE companies SET {sets} WHERE id=${len(args) + 1}", *args, cid)
                if disabled:       # company band => uske sab users ke sessions foran band
                    await conn.execute(
                        "DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE company_id=$1)", cid)
        return True

    # ---------- users ----------
    async def create_user(self, username, password, role="user", collections=None, company=None) -> dict:
        if not USERNAME_RE.match(username):
            raise ValueError("username must be 3-64 chars: letters, digits, _ . @ -")
        if not (MIN_PASSWORD <= len(password) <= MAX_PASSWORD):
            raise ValueError(f"password must be {MIN_PASSWORD}-{MAX_PASSWORD} characters")
        if role not in ROLES:
            raise ValueError("role must be user, company_admin or admin")
        cols = _validate_collections(collections)
        if role == "admin":
            if company is not None:
                raise ValueError("super admin cannot belong to a company")
        else:
            if company is None:
                raise ValueError("company is required for user / company_admin")
            cols = None          # company users ki access company se nikalti hai
        pw_hash = await asyncio.to_thread(hash_password, password, self.scrypt_n)

        try:
            async with self.db.pool.acquire() as conn:
                async with conn.transaction():
                    company_id = None
                    if role != "admin":
                        crow = await conn.fetchrow(
                            "SELECT id, max_users FROM companies WHERE slug=$1 FOR UPDATE", company)
                        if crow is None:
                            raise ValueError("company not found")
                        company_id = crow["id"]
                        if crow["max_users"] > 0:
                            n = await conn.fetchval("SELECT COUNT(*) FROM users WHERE company_id=$1", company_id)
                            if n >= crow["max_users"]:
                                raise ValueError("company user limit reached")
                    await conn.execute(
                        "INSERT INTO users (username, password_hash, role, collections, company_id, created_at) "
                        "VALUES ($1,$2,$3,$4,$5,$6)", username, pw_hash, role, cols, company_id, time.time())
        except ValueError:
            raise
        except Exception as exc:
            if _is_unique(exc):
                raise ValueError("username already exists")
            raise
        self.has_users = True
        row = await self.db.pool.fetchrow(_USER_SELECT + " WHERE lower(u.username)=lower($1)", username)
        return _public(tuple(row))

    async def authenticate(self, username: str, password: str) -> dict | None:
        if not self._ready or len(password) > MAX_PASSWORD:
            return None
        row = await self.db.pool.fetchrow(_USER_SELECT + " WHERE lower(u.username)=lower($1)", username)
        if row is None:
            await asyncio.to_thread(verify_password, password, self._dummy_hash)     # timing barabar rakho
            return None
        row = tuple(row)
        ok = await asyncio.to_thread(verify_password, password, row[2])
        user = _public(row)
        return user if ok and not user["disabled"] else None

    async def update_user(self, username, *, password=None, role=None,
                          collections=_KEEP, disabled=None, only_company=None) -> bool:
        """only_company: company_admin ke liye; sirf usi company ke users badal sakte hain."""
        new_hash = None
        if password is not None:
            if not (MIN_PASSWORD <= len(password) <= MAX_PASSWORD):
                raise ValueError(f"password must be {MIN_PASSWORD}-{MAX_PASSWORD} characters")
            new_hash = await asyncio.to_thread(hash_password, password, self.scrypt_n)
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    _USER_SELECT + " WHERE lower(u.username)=lower($1) FOR UPDATE OF u", username)
                if row is None:
                    return False
                row = tuple(row)
                if only_company is not None and row[7] != only_company:
                    return False             # doosri company ka user: "not found"
                pairs: list[tuple[str, object]] = []
                if new_hash is not None:
                    pairs.append(("password_hash", new_hash))
                if role is not None:
                    if role not in ROLES:
                        raise ValueError("role must be user, company_admin or admin")
                    if (role == "admin") != (row[3] == "admin"):
                        raise ValueError("cannot convert between super admin and company roles; create a new account")
                    pairs.append(("role", role))
                if collections is not _KEEP:
                    if row[7] is not None:
                        if collections:
                            raise ValueError("company users get access from their company; collections not allowed")
                    else:
                        pairs.append(("collections", _validate_collections(collections)))
                if disabled is not None:
                    pairs.append(("disabled", 1 if disabled else 0))
                if pairs:
                    sets, args = _set_clause(pairs)
                    await conn.execute(f"UPDATE users SET {sets} WHERE id=${len(args) + 1}", *args, row[0])
                if password is not None or disabled or role is not None:
                    await conn.execute("DELETE FROM sessions WHERE user_id=$1", row[0])   # purane sessions band
        return True

    async def change_password(self, username: str, current: str, new: str) -> int:
        """Returns user_id. ValueError: galat current password / weak new / account nahi."""
        if not self._ready:
            raise ValueError("accounts not available")
        row = await self.db.pool.fetchrow(
            "SELECT id, password_hash, disabled FROM users WHERE lower(username)=lower($1)", username)
        if row is None or row["disabled"]:
            raise ValueError("password change is only available for user accounts")
        if not await asyncio.to_thread(verify_password, current, row["password_hash"]):
            raise ValueError("current password is incorrect")
        if not (MIN_PASSWORD <= len(new) <= MAX_PASSWORD):
            raise ValueError(f"password must be {MIN_PASSWORD}-{MAX_PASSWORD} characters")
        if new == current:
            raise ValueError("new password must be different")
        new_hash = await asyncio.to_thread(hash_password, new, self.scrypt_n)
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE users SET password_hash=$1 WHERE id=$2", new_hash, row["id"])
                await conn.execute("DELETE FROM sessions WHERE user_id=$1", row["id"])   # sab purane sessions band
        return int(row["id"])

    async def list_users(self, company: str | None = None) -> list[dict]:
        """company=None => sab (sirf super admin ke liye); warna sirf us company ke."""
        if company is None:
            rows = await self.db.pool.fetch(_USER_SELECT + " ORDER BY u.username")
        else:
            rows = await self.db.pool.fetch(_USER_SELECT + " WHERE c.slug=$1 ORDER BY u.username", company)
        return [_public(tuple(r)) for r in rows]

    async def list_online(self, window: int = ONLINE_WINDOW) -> list[dict]:
        """Kaun is waqt active hai. Sirf naam/company, content nahi."""
        now = time.time()
        rows = await self.db.pool.fetch(
            "SELECT u.username, u.role, c.slug, MAX(s.last_used) AS seen "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "LEFT JOIN companies c ON c.id = u.company_id "
            "WHERE s.expires_at > $1 AND s.last_used > $2 "
            "GROUP BY u.id, u.username, u.role, c.slug ORDER BY seen DESC", now, now - window)
        return [{"username": r[0], "role": r[1], "company": r[2], "idle_seconds": int(now - r[3])} for r in rows]

    # ---------- sessions ----------
    async def create_session(self, user_id: int) -> tuple[str, float]:
        token = secrets.token_urlsafe(32)
        now = time.time()
        expires = now + self.session_ttl
        await self.db.pool.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_used) VALUES ($1,$2,$3,$4,$5)",
            _token_hash(token), user_id, now, expires, now)
        return token, expires

    async def get_session_user(self, token: str) -> dict | None:
        if not self._ready or not token:
            return None
        now = time.time()
        th = _token_hash(token)
        row = await self.db.pool.fetchrow(
            "SELECT u.id, u.username, u.password_hash, u.role, u.collections, u.disabled, "
            "u.created_at, c.slug, c.disabled, c.name, s.expires_at "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "LEFT JOIN companies c ON c.id = u.company_id WHERE s.token_hash=$1", th)
        if row is None:
            return None
        row = tuple(row)
        if row[10] < now:
            return None
        user = _public(row[:10])
        if user["disabled"]:
            return None
        await self.db.pool.execute("UPDATE sessions SET last_used=$1 WHERE token_hash=$2", now, th)
        return user

    async def delete_session(self, token: str) -> None:
        if self._ready and token:
            await self.db.pool.execute("DELETE FROM sessions WHERE token_hash=$1", _token_hash(token))
