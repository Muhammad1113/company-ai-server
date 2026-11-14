"""
openFDA (US) live sync with admin approval. Kuch bhi seedha RAG mein nahi jata:

  fetch -> hash compare (approved snapshot se) -> PENDING update -> super admin approve -> tab hi reg__fda mein ingest.

Reject kiya hua (key, hash) dobara propose nahi hota; record badle to naya hash => dobara propose hota hai.
Sirf public FDA data. Kisi company ka data yahan se kabhi nahi guzarta.
"""
import asyncio
import hashlib
import json
import logging
import re

import httpx

logger = logging.getLogger("company_ai.openfda")

CODE_RE = re.compile(r"^[A-Z0-9]{3}$")
FDA_COLLECTION = "reg__fda"

# kind -> (endpoint, search/sort, id field, whitelisted fields). Sirf yehi fields store hote hain.
KINDS = {
    "classification": {
        "path": "/device/classification.json",
        "id": "product_code",
        "sort": None,
        "fields": ["product_code", "device_name", "device_class", "regulation_number",
                   "medical_specialty_description", "definition", "implant_flag", "life_sustain_support_flag"],
        "title": "Device classification",
    },
    "510k": {
        "path": "/device/510k.json",
        "id": "k_number",
        "sort": "decision_date:desc",
        "fields": ["k_number", "device_name", "applicant", "decision_date", "decision_description",
                   "clearance_type", "product_code", "statement_or_summary"],
        "title": "510(k) clearances",
    },
    "recall": {
        "path": "/device/recall.json",
        "id": "res_event_number",
        "sort": "event_date_initiated:desc",
        "fields": ["res_event_number", "product_description", "reason_for_recall", "recall_status",
                   "event_date_initiated", "firm_name", "root_cause_description", "product_code"],
        "title": "Recalls",
    },
}

_MAX_FIELD = 1200


class OpenFDAError(Exception):
    pass


def _clean(value) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:_MAX_FIELD]


def pick(kind: str, raw: dict) -> dict:
    return {f: _clean(raw[f]) for f in KINDS[kind]["fields"] if raw.get(f) not in (None, "", [])}


def record_hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


class OpenFDAClient:

    def __init__(self, base_url: str, api_key: str = "", limit: int = 25, timeout: float = 20.0,
                 transport: httpx.AsyncBaseTransport | None = None, delay: float = 0.3):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.limit = limit
        self.timeout = timeout
        self.transport = transport
        self.delay = delay

    async def fetch(self, kind: str, code: str) -> list[dict]:
        spec = KINDS[kind]
        params = {"search": f'product_code:"{code}"', "limit": 1 if kind == "classification" else self.limit}
        if spec["sort"]:
            params["sort"] = spec["sort"]
        if self.api_key:
            params["api_key"] = self.api_key
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as http:
            res = await http.get(self.base_url + spec["path"], params=params)
        if res.status_code == 404:                     # openFDA: "No matches found!"
            return []
        if res.status_code == 429:
            raise OpenFDAError("openFDA rate limit reached; try again later")
        if res.status_code >= 400:
            raise OpenFDAError(f"openFDA returned HTTP {res.status_code}")
        try:
            results = res.json().get("results") or []
        except ValueError:
            raise OpenFDAError("openFDA returned an unreadable response")
        out = []
        for raw in results:
            data = pick(kind, raw)
            rid = data.get(spec["id"])
            if not rid:
                continue
            out.append({"key": f"{kind}:{rid}", "kind": kind, "code": code, "data": data, "hash": record_hash(data)})
        return out


def render(kind: str, code: str, records: list[dict]) -> str:
    """Approved records -> ek document ka plain text (RAG ke liye). Har record ek chhota paragraph."""
    title = KINDS[kind]["title"]
    lines = [f"US FDA (openFDA) - {title} for product code {code}. Source: api.fda.gov, approved by an administrator.", ""]
    for rec in sorted(records, key=lambda r: r["key"], reverse=True):
        d = rec["data"]
        bits = [f"{k.replace('_', ' ')}: {v}" for k, v in d.items()]
        lines.append(f"[{rec['key']}] " + "; ".join(bits))
        lines.append("")
    return "\n".join(lines).strip()


def doc_id_for(kind: str, code: str) -> str:
    return f"openfda-{code.lower()}-{kind}"


def diff_fields(old: dict | None, new: dict) -> list[dict]:
    if old is None:
        return []
    return [{"field": k, "old": old.get(k), "new": new.get(k)}
            for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)]


async def build_changes(client: OpenFDAClient, repo, codes: list[str]) -> tuple[list[dict], list[dict]]:
    """(changes, errors). Approved snapshot aur ignored list se compare."""
    snapshot = await repo.snapshot()
    ignored = await repo.ignored()
    changes, errors = [], []
    for code in codes:
        for kind in KINDS:
            try:
                fetched = await client.fetch(kind, code)
            except (OpenFDAError, httpx.HTTPError) as exc:
                msg = str(exc) or type(exc).__name__
                errors.append({"code": code, "kind": kind, "error": msg})
                if "rate limit" in msg:
                    return changes, errors            # aage mat jao
                continue
            for rec in fetched:
                old = snapshot.get(rec["key"])
                if old and old["hash"] == rec["hash"]:
                    continue
                if (rec["key"], rec["hash"]) in ignored:
                    continue
                changes.append({**rec, "change": "changed" if old else "new",
                                "old": old["data"] if old else None,
                                "fields": diff_fields(old["data"] if old else None, rec["data"])})
            if client.delay:
                await asyncio.sleep(client.delay)
    return changes, errors


def summarize(changes: list[dict]) -> dict:
    by_code: dict[str, dict] = {}
    for c in changes:
        b = by_code.setdefault(c["code"], {"new": 0, "changed": 0})
        b[c["change"]] += 1
    return {"total": len(changes), "new": sum(1 for c in changes if c["change"] == "new"),
            "changed": sum(1 for c in changes if c["change"] == "changed"), "by_code": by_code}
