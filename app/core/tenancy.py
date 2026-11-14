"""
Multi-company (tenant) access policy. Pure module: koi framework import nahi, is liye seedha test hota hai.

Collection naming (yehi isolation ki buniyad hai):
  co_<company>__<naam>   company ke apne documents      (sirf wohi company dekh/badal sakti hai)
  reg__<naam>            shared regulatory (fda, eu, uk, au ...)   (sab padh sakte hain, sirf super admin badalta hai)
  koi aur naam           legacy: sirf super admin; company users ko kabhi nahi milta (deny by default)

Roles:
  admin          super admin (sab kuch)
  company_admin  apni company ke users aur documents
  user           apni company ke documents + reg__ (sirf padhna)
"""
import re

# a-z 0-9 aur single '_' ; '__' mana hai taake "co_<slug>__" prefix kabhi dusri company ka prefix na bane
SLUG_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
SLUG_MIN, SLUG_MAX = 2, 32
COLLECTION_RE = re.compile(r"^[\w.\-:]+$")

SHARED_PREFIX = "reg__"
ROLES = ("user", "company_admin", "admin")


def valid_slug(slug: str) -> bool:
    return isinstance(slug, str) and SLUG_MIN <= len(slug) <= SLUG_MAX and bool(SLUG_RE.match(slug))


def company_prefix(slug: str) -> str:
    if not valid_slug(slug):
        raise ValueError("invalid company slug")
    return f"co_{slug}__"


def company_collection(slug: str, name: str = "docs") -> str:
    full = company_prefix(slug) + name
    if not COLLECTION_RE.match(full) or "__" in name:
        raise ValueError("invalid collection name")
    return full


def is_shared(collection: str) -> bool:
    return (
        isinstance(collection, str)
        and collection.startswith(SHARED_PREFIX)
        and len(collection) > len(SHARED_PREFIX)
    )


def owner_of(collection: str) -> str | None:
    """'co_acme__docs' -> 'acme'. Shared / legacy collection => None."""
    if not isinstance(collection, str) or not collection.startswith("co_") or "__" not in collection[3:]:
        return None
    slug, rest = collection[3:].split("__", 1)
    return slug if rest and valid_slug(slug) else None


def can_read(role: str, company: str | None, explicit: frozenset | None, collection: str) -> bool:
    if not isinstance(collection, str) or not COLLECTION_RE.match(collection):
        return False
    if role == "admin":
        return True
    if is_shared(collection):
        return True
    if company is not None:
        return owner_of(collection) == company          # company user: sirf apni; explicit list ignore
    # company ke baghair (purane users / API keys): sirf saaf likhi hui list, "khali = sab" ab nahi
    return bool(explicit) and collection in explicit


def can_write(role: str, company: str | None, collection: str) -> bool:
    if not isinstance(collection, str) or not COLLECTION_RE.match(collection):
        return False
    if role == "admin":
        return True
    if role == "company_admin" and company is not None:
        return owner_of(collection) == company
    return False
