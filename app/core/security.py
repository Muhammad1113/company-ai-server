"""API key authentication + roles. Pure module (settings import nahi karta)."""
import hmac
from dataclasses import dataclass

MIN_KEY_LENGTH = 16
ROLES = {"user", "admin"}


@dataclass(frozen=True)
class ClientInfo:
    name: str
    role: str = "user"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def parse_api_keys(raw: str) -> list[tuple[str, ClientInfo]]:
    """
    Format: "name:key[:role],name2:key2[:role]"   (role: user|admin)
    Key mein ':' ya ',' nahi hona chahiye. Empty string => auth disabled.
    """
    entries: list[tuple[str, ClientInfo]] = []
    names, keys = set(), set()

    for index, item in enumerate((raw or "").split(","), start=1):
        item = item.strip()
        if not item:
            continue

        parts = item.split(":")
        if len(parts) not in (2, 3):
            raise ValueError(
                f"API_KEYS entry #{index} invalid. Use name:key[:role]"
            )

        name, key = parts[0].strip(), parts[1].strip()
        role = parts[2].strip().lower() if len(parts) == 3 else "user"

        if not name:
            raise ValueError(f"API_KEYS entry #{index}: empty name")
        if len(key) < MIN_KEY_LENGTH:
            raise ValueError(
                f"API_KEYS entry #{index}: key must be at least "
                f"{MIN_KEY_LENGTH} characters"
            )
        if role not in ROLES:
            raise ValueError(f"API_KEYS entry #{index}: role must be user|admin")
        if name in names or key in keys:
            raise ValueError(f"API_KEYS entry #{index}: duplicate name or key")

        names.add(name)
        keys.add(key)
        entries.append((key, ClientInfo(name=name, role=role)))

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
