"""API key authentication + roles. Pure module (settings import nahi karta)."""
import hmac
import re
from dataclasses import dataclass

from app.core import tenancy

MIN_KEY_LENGTH = 16
API_KEY_ROLES = {"user", "admin"}      # company_admin ke liye login account chahiye (company se bandha)
_COLLECTION = re.compile(r"^[\w.\-:]+$")


@dataclass(frozen=True)
class ClientInfo:
    name: str
    role: str = "user"
    # Sirf saaf likhi hui extra collections (company ke baghair wale API keys ke liye).
    # None ya khali = KUCH NAHI (pehle "sab" tha; ab deny by default). Super admin hamesha sab.
    collections: frozenset | None = None
    company: str | None = None          # company slug; super admin ke liye None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_company_admin(self) -> bool:
        return self.role == "company_admin" and self.company is not None

    def can_access(self, collection: str) -> bool:
        return tenancy.can_read(self.role, self.company, self.collections, collection)

    def can_manage(self, collection: str) -> bool:
        return tenancy.can_write(self.role, self.company, collection)


def parse_api_keys(raw: str) -> list[tuple[str, ClientInfo]]:
    """
    Format: "name:key[:role[:collections]],..."   role: user|admin
    collections: "hr|finance". Khali ya "*" => koi extra collection nahi (deny by default;
    shared reg__* har authenticated client ko milti hain). Company users ke liye login accounts use karo.
    Key mein ':' ',' '|' nahi hona chahiye. Empty string => auth disabled.
    """
    entries: list[tuple[str, ClientInfo]] = []
    names, keys = set(), set()

    for index, item in enumerate((raw or "").split(","), start=1):
        item = item.strip()
        if not item:
            continue

        parts = item.split(":")
        if len(parts) not in (2, 3, 4):
            raise ValueError(
                f"API_KEYS entry #{index} invalid. Use name:key[:role[:collections]]"
            )

        name, key = parts[0].strip(), parts[1].strip()
        role = parts[2].strip().lower() if len(parts) >= 3 else "user"

        if not name:
            raise ValueError(f"API_KEYS entry #{index}: empty name")
        if len(key) < MIN_KEY_LENGTH:
            raise ValueError(
                f"API_KEYS entry #{index}: key must be at least "
                f"{MIN_KEY_LENGTH} characters"
            )
        if role not in API_KEY_ROLES:
            raise ValueError(f"API_KEYS entry #{index}: role must be user|admin")

        collections = None
        if len(parts) == 4 and parts[3].strip() not in ("", "*"):
            names_list = [c.strip() for c in parts[3].split("|")]
            if not all(_COLLECTION.match(c) for c in names_list):
                raise ValueError(f"API_KEYS entry #{index}: invalid collection name")
            collections = frozenset(names_list)
        if name in names or key in keys:
            raise ValueError(f"API_KEYS entry #{index}: duplicate name or key")

        names.add(name)
        keys.add(key)
        entries.append(
            (key, ClientInfo(name=name, role=role, collections=collections))
        )

    return entries


def extract_api_key(headers) -> str | None:
    """`Authorization: Bearer <key>` ya `X-API-Key: <key>`."""
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return (headers.get("x-api-key") or "").strip() or None


def authenticate(provided, entries) -> ClientInfo | None:
    if not provided:
        return None

    provided_bytes = provided.encode()
    match = None

    # Sab keys check hoti hain (constant-time compare, early exit nahi)
    for key, info in entries:
        if hmac.compare_digest(provided_bytes, key.encode()):
            match = info

    return match
