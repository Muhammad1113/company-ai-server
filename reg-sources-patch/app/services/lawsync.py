"""
Law-text sources (UK legislation.gov.uk, EUR-Lex) ka shared hissa. Flow eCFR jaisa hi hai:

  fetch -> section-by-section hash compare (approved snapshot se) -> PENDING update -> super admin approve -> tab hi RAG.

Har source (uklegislation.py, eurlex.py) sirf "fetch + parse" karta hai; diff / removed / safety guards yahan ek jagah hain.
Kuch bhi seedha RAG mein nahi jata.
"""
import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.openfda import diff_fields, record_hash

logger = logging.getLogger("company_ai.lawsync")

MAX_PART_CHARS = 30000          # ek record ka text is se lamba ho to parts mein tootta hai (kabhi truncate nahi)
GUARD_MIN_KNOWN = 10            # itne approved records hon tab "achanak bohat kam" wali hifazat lagti hai
GUARD_RATIO = 0.5
USER_AGENT = "CompanyAI-RegSync/1.0 (public regulatory text sync)"


class LawSourceError(Exception):
    pass


@dataclass
class LawSource:
    """Service ko batata hai ke ek source kaun sa module (parse/render) aur kaun sa client use karta hai."""
    name: str            # "uk" / "eu": route ka naam aur pending.source ki value
    module: Any          # KIND, COLLECTION, SOURCE_HOST, DEFAULT_ITEMS, normalize_code(), render(), doc_id_for(), item_name()
    client: Any          # async fetch_act(code) -> (records, meta); delay
    max_items: int = 12


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def norm_ws(text: str) -> str:
    return " ".join((text or "").replace("\u00a0", " ").split())


def natural_key(key: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", key)]


def split_text(text: str, limit: int = MAX_PART_CHARS) -> list[str]:
    """Lamba text paragraph ki hadd par parts mein. Kuch bhi gira nahi deta (qanoon ka text adhura nahi hona chahiye)."""
    parts: list[str] = []
    cur: list[str] = []
    size = 0

    def flush():
        nonlocal cur, size
        if cur:
            parts.append("\n".join(cur))
        cur, size = [], 0

    for para in (p for p in (text or "").split("\n") if p.strip()):
        while len(para) > limit:                      # ek hi paragraph hadd se bara
            cut = para.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            flush()
            parts.append(para[:cut].strip())
            para = para[cut:].strip()
        if size + len(para) + 1 > limit and cur:
            flush()
        cur.append(para)
        size += len(para) + 1
    flush()
    return parts or [""]


def unique_key(seen: set[str], key: str) -> str:
    if key in seen:
        i = 2
        while f"{key}~{i}" in seen:
            i += 1
        key = f"{key}~{i}"
    seen.add(key)
    return key


def make_records(kind: str, code: str, key: str, data: dict, text: str) -> list[dict]:
    """Ek section -> 1 ya zyada record (lamba ho to -p2, -p3 ...). Har record ka apna hash hota hai."""
    parts = split_text(text)
    out = []
    for i, part in enumerate(parts, 1):
        d = {**data, "text": part}
        if len(parts) > 1:
            d["part"], d["parts"] = i, len(parts)
        k = key if i == 1 else f"{key}-p{i}"
        out.append({"key": k, "kind": kind, "code": code, "data": d, "hash": record_hash(d)})
    return out


def reject_entities(xml_bytes: bytes):
    """Gov data par bhi XML entity-expansion ka darwaza band: <!ENTITY wala XML hum parse hi nahi karte."""
    if b"<!ENTITY" in xml_bytes:
        raise LawSourceError("response contains XML entity declarations; refusing to parse it")


async def build_changes(fetch, kind: str, repo, codes: list[str], delay: float = 0.0):
    """
    fetch: async (code) -> (records, meta).  Returns (changes, errors, metas).
    Approved snapshot aur ignored (rejected) list se compare. Hate hue sections 'removed' hote hain.
    Hifazat: agar naya fetch approved version se bohat chhota hai (parse tootne ka shak), to koi 'removed' propose nahi hota.
    """
    snapshot = await repo.snapshot()
    ignored = await repo.ignored()
    changes, errors, metas = [], [], {}
    for code in codes:
        try:
            fetched, meta = await fetch(code)
        except (LawSourceError, httpx.HTTPError) as exc:
            msg = str(exc) or type(exc).__name__
            errors.append({"code": code, "kind": kind, "error": msg})
            if "rate limit" in msg:
                return changes, errors, metas              # aage mat jao
            continue
        known = [r for r in snapshot.values() if r["kind"] == kind and r["code"] == code]
        if len(known) >= GUARD_MIN_KNOWN and len(fetched) < len(known) * GUARD_RATIO:
            errors.append({"code": code, "kind": kind, "error": (
                f"the fetched text has far fewer sections ({len(fetched)}) than the approved version ({len(known)}); "
                "nothing was proposed. The source layout may have changed; run the live check script.")})
            continue
        metas[code] = meta or {}
        now_keys = set()
        for rec in fetched:
            now_keys.add(rec["key"])
            old = snapshot.get(rec["key"])
            if old and old["hash"] == rec["hash"]:
                continue
            if (rec["key"], rec["hash"]) in ignored:
                continue
            changes.append({**rec, "change": "changed" if old else "new",
                            "old": old["data"] if old else None,
                            "fields": diff_fields(old["data"] if old else None, rec["data"])})
        for old in known:                                   # pehle approved tha, ab source mein nahi
            if old["key"] in now_keys:
                continue
            h = "removed-" + old["hash"]
            if (old["key"], h) in ignored:
                continue
            changes.append({"key": old["key"], "kind": kind, "code": code, "data": old["data"], "hash": h,
                            "change": "removed", "old": old["data"], "fields": []})
        if delay:
            await asyncio.sleep(delay)
    return changes, errors, metas


def summarize(changes: list[dict], metas: dict, names: dict[str, str] | None = None) -> dict:
    """Admin ke review screen ke liye khulasa. names: code -> dikhane wala naam."""
    names = names or {}
    by_code: dict[str, dict] = {}
    for c in changes:
        b = by_code.setdefault(names.get(c["code"], c["code"]), {"new": 0, "changed": 0, "removed": 0})
        b[c["change"]] += 1
    return {"total": len(changes), "new": sum(1 for c in changes if c["change"] == "new"),
            "changed": sum(1 for c in changes if c["change"] == "changed"),
            "removed": sum(1 for c in changes if c["change"] == "removed"),
            "by_code": by_code, "meta": metas}


def render_records(tag_for, records: list[dict], order_key) -> list[str]:
    """Approved records -> plain text lines. Har paragraph par [tag] (retrieval + citation ke liye)."""
    lines: list[str] = []
    for rec in sorted(records, key=lambda r: order_key(r["key"])):
        d = rec["data"]
        tag = f"[{tag_for(d)}]"
        if d.get("part", 1) == 1 and (d.get("heading") or d.get("context")):
            ctx = f" ({d['context']})" if d.get("context") else ""
            lines += [f"{tag} {d.get('heading') or ''}{ctx}".strip(), ""]
        for para in (d.get("text") or "").split("\n"):
            if para.strip():
                lines += [f"{tag} {para.strip()}", ""]
    return lines
