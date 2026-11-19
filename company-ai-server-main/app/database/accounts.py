"""
User accounts + login sessions (SQLite, stdlib). Interface Postgres ke liye wahi rahega.

- Password: scrypt (stdlib), per-user random salt.
- Session token: random 256-bit; DB mein sirf SHA-256 hash (DB leak se token nahi milta).
- Login timing: user na ho tab bhi dummy hash verify (user enumeration se bachne ke liye).
"""
import asyncio
import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
import threading
import time

from app.core import tenancy

logger = logging.getLogger("company_ai.accounts")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{3,64}$")
COLLECTION_RE = re.compile(r"^[\w.\-:]+$")
MIN_PASSWORD = 8
MAX_PASSWORD = 128          # scrypt DoS se bachne ke liye cap
ROLES = ("user", "company_admin", "admin")
ONLINE_WINDOW = 300          # sessions.last_used is se purana ho to "online" nahi
_KEEP = object()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    email TEXT,
    disabled INTEGER NOT NULL DEFAULT 0,
    max_users INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    collections TEXT,
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    last_used REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);
"""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str, n: int = 2 ** 14, r: int = 8, p: int = 1) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p,
                        dklen=32, maxmem=128 * 1024 * 1024)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                            n=int(n), r=int(r), p=int(p), dklen=32,
                            maxmem=128 * 1024 * 1024)
        return hmac.compare_digest(dk, base64.b64decode(expected))
    except Exception:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _parse_collections(value: str | None) -> list[str] | None:
    return [c for c in value.split("|") if c] if value else None


_USER_SELECT = (
    "SELECT u.id, u.username, u.password_hash, u.role, u.collections, u.disabled, "
    "u.created_at, c.slug, c.disabled, c.name "
    "FROM users u LEFT JOIN companies c ON c.id = u.company_id"
)


def _public(row) -> dict:
    """row = _USER_SELECT ka natija. disabled = user band YA uski company band."""
    company_disabled = bool(row[8]) if row[7] is not None else False
    return {
        "id": row[0], "username": row[1], "role": row[3],
        "collections": _parse_collections(row[4]),
        "disabled": bool(row[5]) or company_disabled,
        "company": row[7], "company_name": row[9],
    }


def _company_public(row) -> dict:
    return {"id": row[0], "slug": row[1], "name": row[2], "email": row[3],
            "disabled": bool(row[4]), "max_users": row[5], "created_at": row[6]}


def _validate_collections(collections):
    if collections is None:
        return None
    cleaned = [c.strip() for c in collections if c and c.strip()]
    if not all(COLLECTION_RE.match(c) for c in cleaned):
        raise ValueError("invalid collection name")
    return "|".join(cleaned) or None


class AccountsRepository:

    def __init__(self, path: str, session_ttl_hours: float = 168.0,
                 scrypt_n: int = 2 ** 14):
        self.path = path
        self.session_ttl = session_ttl_hours * 3600
        self.scrypt_n = scrypt_n
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._dummy_hash = hash_password("dummy-password", n=scrypt_n)
        self.has_users = False

    # ---------- lifecycle ----------
    def _init_sync(self):
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(users)").fetchall()}
        if "company_id" not in cols:                      # purana DB: migration
            self._conn.execute("ALTER TABLE users ADD COLUMN company_id INTEGER REFERENCES companies(id)")
        self._conn.commit()
        self.has_users = self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
        self._conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
        self._conn.commit()

    async def init(self):
        await asyncio.to_thread(self._init_sync)

    async def close(self):
        if self._conn is not None:
            conn, self._conn = self._conn, None
            await asyncio.to_thread(conn.close)

    def _q(self, sql, args=(), one=False, commit=False):
        with self._lock:
            cur = self._conn.execute(sql, args)
            result = cur.fetchone() if one else cur.fetchall()
            if commit:
                self._conn.commit()
            return result, cur.rowcount

    # ---------- companies ----------
    def _create_company_sync(self, slug, name, email, max_users):
        if not tenancy.valid_slug(slug):
            raise ValueError("company slug: 2-32 chars, a-z 0-9 and single underscores")
        name = (name or "").strip()
        if not name or len(name) > 120:
            raise ValueError("company name required (max 120 chars)")
        email = (email or "").strip() or None
        if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            raise ValueError("invalid company email")
        if max_users < 0:
            raise ValueError("max_users must be >= 0 (0 = unlimited)")
        try:
            self._q("INSERT INTO companies (slug, name, email, max_users, created_at) VALUES (?,?,?,?,?)",
                    (slug, name, email, max_users, time.time()), commit=True)
        except sqlite3.IntegrityError:
            raise ValueError("company already exists")
        return self._get_company_sync(slug)

    async def create_company(self, slug, name, email=None, max_users=0) -> dict:
        return await asyncio.to_thread(self._create_company_sync, slug, name, email, max_users)

    def _get_company_sync(self, slug):
        row, _ = self._q("SELECT * FROM companies WHERE slug=?", (slug,), one=True)
        return _company_public(row) if row else None

    async def get_company(self, slug: str) -> dict | None:
        return await asyncio.to_thread(self._get_company_sync, slug)

    def _list_companies_sync(self):
        now = time.time()
        rows, _ = self._q(
            "SELECT c.*, "
            " (SELECT COUNT(*) FROM users u WHERE u.company_id = c.id), "
            " (SELECT COUNT(DISTINCT s.user_id) FROM sessions s JOIN users u2 ON u2.id = s.user_id "
            "   WHERE u2.company_id = c.id AND s.expires_at > ? AND s.last_used > ?) "
            "FROM companies c ORDER BY c.slug", (now, now - ONLINE_WINDOW))
        return [{**_company_public(r), "users": r[7], "online": r[8],
                 "collection": tenancy.company_collection(r[1])} for r in rows]

    async def list_companies(self) -> list[dict]:
        return await asyncio.to_thread(self._list_companies_sync)

    def _update_company_sync(self, slug, name, email, disabled, max_users):
        row, _ = self._q("SELECT * FROM companies WHERE slug=?", (slug,), one=True)
        if row is None:
            return False
        sets, args = [], []
        if name is not None:
            if not name.strip() or len(name) > 120:
                raise ValueError("invalid company name")
            sets.append("name=?"); args.append(name.strip())
        if email is not _KEEP:
            email = (email or "").strip() or None
            if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
                raise ValueError("invalid company email")
            sets.append("email=?"); args.append(email)
        if disabled is not None:
            sets.append("disabled=?"); args.append(1 if disabled else 0)
        if max_users is not None:
            if max_users < 0:
                raise ValueError("max_users must be >= 0")
            sets.append("max_users=?"); args.append(max_users)
        if sets:
            self._q(f"UPDATE companies SET {', '.join(sets)} WHERE id=?", (*args, row[0]), commit=True)
        if disabled:       # company band => uske sab users ke sessions foran band
            self._q("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE company_id=?)",
                    (row[0],), commit=True)
        return True

    async def update_company(self, slug, *, name=None, email=_KEEP, disabled=None, max_users=None) -> bool:
        return await asyncio.to_thread(self._update_company_sync, slug, name, email, disabled, max_users)

    # ---------- users ----------
    def _create_user_sync(self, username, password, role, collections, company):
        if not USERNAME_RE.match(username):
            raise ValueError("username must be 3-64 chars: letters, digits, _ . @ -")
        if not (MIN_PASSWORD <= len(password) <= MAX_PASSWORD):
            raise ValueError(f"password must be {MIN_PASSWORD}-{MAX_PASSWORD} characters")
        if role not in ROLES:
            raise ValueError("role must be user, company_admin or admin")
        cols = _validate_collections(collections)

        company_id = None
        if role == "admin":
            if company is not None:
                raise ValueError("super admin cannot belong to a company")
        else:
            if company is None:
                raise ValueError("company is required for user / company_admin")
            crow, _ = self._q("SELECT id, max_users FROM companies WHERE slug=?", (company,), one=True)
            if crow is None:
                raise ValueError("company not found")
            company_id = crow[0]
            if crow[1] > 0:
                n, _ = self._q("SELECT COUNT(*) FROM users WHERE company_id=?", (company_id,), one=True)
                if n[0] >= crow[1]:
                    raise ValueError("company user limit reached")
            cols = None          # company users ki access company se nikalti hai, haath ki list se nahi

        try:
            self._q("INSERT INTO users (username, password_hash, role, collections, company_id, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (username, hash_password(password, n=self.scrypt_n), role, cols, company_id, time.time()),
                    commit=True)
        except sqlite3.IntegrityError:
            raise ValueError("username already exists")

        self.has_users = True
        row, _ = self._q(_USER_SELECT + " WHERE u.username=?", (username,), one=True)
        return _public(row)

    async def create_user(self, username, password, role="user", collections=None, company=None) -> dict:
        return await asyncio.to_thread(self._create_user_sync, username, password, role, collections, company)

    def _authenticate_sync(self, username, password):
        row, _ = self._q(_USER_SELECT + " WHERE u.username=?", (username,), one=True)
        if row is None:
            verify_password(password, self._dummy_hash)        # timing barabar rakho
            return None
        ok = verify_password(password, row[2])
        user = _public(row)
        return user if ok and not user["disabled"] else None

    async def authenticate(self, username: str, password: str) -> dict | None:
        if self._conn is None or len(password) > MAX_PASSWORD:
            return None
        return await asyncio.to_thread(self._authenticate_sync, username, password)

    def _update_user_sync(self, username, password, role, collections, disabled, only_company):
        row, _ = self._q(_USER_SELECT + " WHERE u.username=?", (username,), one=True)
        if row is None:
            return False
        if only_company is not None and row[7] != only_company:
            return False             # doosri company ka user: "not found" (wajood nahi batate)

        sets, args = [], []
        if password is not None:
            if not (MIN_PASSWORD <= len(password) <= MAX_PASSWORD):
                raise ValueError(f"password must be {MIN_PASSWORD}-{MAX_PASSWORD} characters")
            sets.append("password_hash=?"); args.append(hash_password(password, n=self.scrypt_n))
        if role is not None:
            if role not in ROLES:
                raise ValueError("role must be user, company_admin or admin")
            if (role == "admin") != (row[3] == "admin"):
                raise ValueError("cannot convert between super admin and company roles; create a new account")
            sets.append("role=?"); args.append(role)
        if collections is not _KEEP:
            if row[7] is not None:
                if collections:       # company users: access company se aati hai; null/khali bhejna harmless (ignore)
                    raise ValueError("company users get access from their company; collections not allowed")
            else:
                sets.append("collections=?"); args.append(_validate_collections(collections))
        if disabled is not None:
            sets.append("disabled=?"); args.append(1 if disabled else 0)

        if sets:
            self._q(f"UPDATE users SET {', '.join(sets)} WHERE id=?", (*args, row[0]), commit=True)
        if password is not None or disabled or role is not None:
            self._q("DELETE FROM sessions WHERE user_id=?", (row[0],), commit=True)   # purane sessions band
        return True

    async def update_user(self, username, *, password=None, role=None,
                          collections=_KEEP, disabled=None, only_company=None) -> bool:
        """only_company: company_admin ke liye; sirf usi company ke users badal sakte hain."""
        return await asyncio.to_thread(self._update_user_sync, username, password, role,
                                       collections, disabled, only_company)

    def _change_password_sync(self, username, current, new):
        row, _ = self._q("SELECT * FROM users WHERE username=?", (username,), one=True)
        if row is None or row[5]:
            raise ValueError("password change is only available for user accounts")
        if not verify_password(current, row[2]):
            raise ValueError("current password is incorrect")
        if not (MIN_PASSWORD <= len(new) <= MAX_PASSWORD):
            raise ValueError(f"password must be {MIN_PASSWORD}-{MAX_PASSWORD} characters")
        if new == current:
            raise ValueError("new password must be different")

        self._q("UPDATE users SET password_hash=? WHERE id=?",
                (hash_password(new, n=self.scrypt_n), row[0]), commit=True)
        self._q("DELETE FROM sessions WHERE user_id=?", (row[0],), commit=True)   # sab purane sessions band
        return row[0]

    async def change_password(self, username: str, current: str, new: str) -> int:
        """Returns user_id. ValueError: galat current password / weak new / account nahi."""
        if self._conn is None:
            raise ValueError("accounts not available")
        return await asyncio.to_thread(self._change_password_sync, username, current, new)

    async def list_users(self, company: str | None = None) -> list[dict]:
        """company=None => sab (sirf super admin ke liye); warna sirf us company ke."""
        if company is None:
            rows, _ = await asyncio.to_thread(self._q, _USER_SELECT + " ORDER BY u.username")
        else:
            rows, _ = await asyncio.to_thread(
                self._q, _USER_SELECT + " WHERE c.slug=? ORDER BY u.username", (company,))
        return [_public(r) for r in rows]

    def _list_online_sync(self, window):
        now = time.time()
        rows, _ = self._q(
            "SELECT u.username, u.role, c.slug, MAX(s.last_used) AS seen "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "LEFT JOIN companies c ON c.id = u.company_id "
            "WHERE s.expires_at > ? AND s.last_used > ? GROUP BY u.id ORDER BY seen DESC",
            (now, now - window))
        return [{"username": r[0], "role": r[1], "company": r[2], "idle_seconds": int(now - r[3])} for r in rows]

    async def list_online(self, window: int = ONLINE_WINDOW) -> list[dict]:
        """Kaun is waqt active hai (pichle `window` seconds mein koi request). Sirf naam/company, content nahi."""
        return await asyncio.to_thread(self._list_online_sync, window)

    # ---------- sessions ----------
    def _create_session_sync(self, user_id):
        token = secrets.token_urlsafe(32)
        now = time.time()
        expires = now + self.session_ttl
        self._q("INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_used) "
                "VALUES (?,?,?,?,?)", (_token_hash(token), user_id, now, expires, now), commit=True)
        return token, expires

    async def create_session(self, user_id: int) -> tuple[str, float]:
        return await asyncio.to_thread(self._create_session_sync, user_id)

    def _get_session_user_sync(self, token):
        now = time.time()
        row, _ = self._q(
            "SELECT u.id, u.username, u.password_hash, u.role, u.collections, u.disabled, "
            "u.created_at, c.slug, c.disabled, c.name, s.expires_at "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "LEFT JOIN companies c ON c.id = u.company_id WHERE s.token_hash=?",
            (_token_hash(token),), one=True)
        if row is None or row[10] < now:
            return None
        user = _public(row[:10])
        if user["disabled"]:
            return None
        self._q("UPDATE sessions SET last_used=? WHERE token_hash=?", (now, _token_hash(token)), commit=True)
        return user

    async def get_session_user(self, token: str) -> dict | None:
        if self._conn is None or not token:
            return None
        return await asyncio.to_thread(self._get_session_user_sync, token)

    async def delete_session(self, token: str) -> None:
        if self._conn is not None and token:
            await asyncio.to_thread(self._q, "DELETE FROM sessions WHERE token_hash=?",
                                    (_token_hash(token),), False, True)
