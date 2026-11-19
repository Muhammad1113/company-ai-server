"""
eCFR (US Code of Federal Regulations, Title 21) live sync with admin approval. openFDA jaisa hi flow:

  fetch -> section-by-section hash compare (approved snapshot se) -> PENDING update -> super admin approve
  -> tab hi reg__fda mein ingest.

Sirf public qanoon ka text (ecfr.gov, US Government, free, koi API key nahi). Har section ek record hai, is liye
review mein pata chalta hai ke kaun sa section naya / badla / hata. Reject kiya hua (key, hash) dobara propose nahi hota.
"""
import asyncio
import logging
import re
import xml.etree.ElementTree as ET

import httpx

from app.services.openfda import FDA_COLLECTION, diff_fields, record_hash

logger = logging.getLogger("company_ai.ecfr")

PART_RE = re.compile(r"^[0-9]{1,4}$")
KIND = "ecfr"
COLLECTION = FDA_COLLECTION                 # FDA ke saare documents ek hi collection (reg__fda) mein
_MAX_SECTION_CHARS = 30000
_MAX_BYTES = 25 * 1024 * 1024

# Pehli baar khali list ho to yeh 4 parts khud add ho jate hain (admin hata/badal sakta hai).
DEFAULT_PARTS = [
    ("820", "Quality Management System Regulation (QMSR)"),
    ("801", "Labeling"),
    ("807", "Establishment registration, device listing and 510(k)"),
    ("878", "General and plastic surgery devices"),
]


class ECFRError(Exception):
    pass


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _section_id(n_attr: str, heading: str, kind: str) -> str:
    raw = (n_attr or "").replace("\u00a7", " ").strip()
    if not raw:
        m = re.match(r"^\W*(?:\u00a7+\s*)?([\w.\-]+(?:\s+[\w.\-]+)?)", heading or "")
        raw = m.group(1) if m else (heading or kind)
    sid = re.sub(r"[^A-Za-z0-9.\-]+", "-", raw).strip("-").lower()
    return sid[:60] or kind.lower()


def parse_part(xml_bytes: bytes, part: str) -> list[dict]:
    """eCFR 'full' XML -> [{key, kind, code, data, hash}]. Section (DIV8) aur Appendix (DIV9) har ek record."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ECFRError(f"eCFR returned an unreadable response ({exc})")
    out, seen = [], set()
    for el in root.iter():
        typ = (el.get("TYPE") or "").upper()
        if typ not in ("SECTION", "APPENDIX"):
            continue
        head_el = el.find("HEAD")
        heading = _norm("".join(head_el.itertext())) if head_el is not None else ""
        paras = []
        for child in el:
            if child is head_el:
                continue
            txt = _norm("".join(child.itertext()))
            if txt:
                paras.append(txt)
        text = "\n".join(paras)[:_MAX_SECTION_CHARS]
        sid = _section_id(el.get("N") or "", heading, typ)
        key = f"{KIND}:21-{part}-{sid}"
        if key in seen:                                   # kisi wajah se dohra id ho to dusra alag key
            i = 2
            while f"{key}-{i}" in seen:
                i += 1
            key = f"{key}-{i}"
        seen.add(key)
        data = {"part": part, "section": heading.split(" ")[1] if heading.startswith("\u00a7") and " " in heading else sid,
                "heading": heading[:300], "text": text}
        if not heading and not text:
            continue
        out.append({"key": key, "kind": KIND, "code": part, "data": data, "hash": record_hash(data)})
    return out


class ECFRClient:

    def __init__(self, base_url: str = "https://www.ecfr.gov", title: int = 21, timeout: float = 60.0,
                 transport: httpx.AsyncBaseTransport | None = None, delay: float = 0.5):
        self.base_url = base_url.rstrip("/")
        self.title = int(title)
        self.timeout = timeout
        self.transport = transport
        self.delay = delay

    async def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport, follow_redirects=True) as http:
            res = await http.get(self.base_url + path, params=params)
        if res.status_code == 404:
            raise ECFRError("eCFR has no such title or part (HTTP 404)")
        if res.status_code == 429:
            raise ECFRError("eCFR rate limit reached; try again later")
        if res.status_code >= 400:
            raise ECFRError(f"eCFR returned HTTP {res.status_code}")
        if len(res.content) > _MAX_BYTES:
            raise ECFRError("eCFR response is too large")
        return res

    async def as_of_date(self) -> str:
        """Title ki 'up to date' tareekh (eCFR sirf is tareekh tak ka text deta hai)."""
        res = await self._get("/api/versioner/v1/titles.json")
        try:
            for t in res.json().get("titles", []):
                if t.get("number") == self.title:
                    day = t.get("up_to_date_as_of") or t.get("latest_issue_date")
                    if day and re.match(r"^\d{4}-\d{2}-\d{2}$", day):
                        return day
        except ValueError:
            pass
        raise ECFRError("eCFR did not say how current Title %d is" % self.title)

    async def fetch_part(self, part: str, as_of: str) -> list[dict]:
        res = await self._get(f"/api/versioner/v1/full/{as_of}/title-{self.title}.xml", {"part": part})
        records = parse_part(res.content, part)
        if not records:
            raise ECFRError(f"Part {part} had no sections in the eCFR response")
        return records


def _natural(key: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", key)]


def render(part: str, label: str | None, records: list[dict], as_of: str | None) -> str:
    """Approved sections -> ek document ka plain text (RAG ke liye). Har paragraph par 21 CFR tag (retrieval + citation)."""
    title = f"US FDA regulation: 21 CFR Part {part}" + (f" ({label})" if label else "")
    when = f", current as of {as_of}" if as_of else ""
    lines = [f"{title}. Source: eCFR (ecfr.gov){when}, approved by an administrator.", ""]
    for rec in sorted(records, key=lambda r: _natural(r["key"])):
        d = rec["data"]
        sec = d.get("section") or ""
        tag = f"[21 CFR {sec}]" if re.match(r"^\d+\.\d+", sec) else f"[21 CFR Part {part} {(d.get('heading') or sec)[:40]}]"
        if d.get("heading"):
            lines.append(f"{tag} {d['heading']}")
            lines.append("")
        for para in (d.get("text") or "").split("\n"):
            if para.strip():
                lines.append(f"{tag} {para.strip()}")
                lines.append("")
    return "\n".join(lines).strip()


def doc_id_for(part: str) -> str:
    return f"ecfr-21cfr{part}"


async def build_changes(client: ECFRClient, repo, parts: list[str]) -> tuple[list[dict], list[dict], str | None]:
    """(changes, errors, as_of). Approved snapshot aur ignored list se compare; hate hue sections 'removed' hote hain."""
    snapshot = await repo.snapshot()
    ignored = await repo.ignored()
    changes, errors = [], []
    try:
        as_of = await client.as_of_date()
    except (ECFRError, httpx.HTTPError) as exc:
        return [], [{"code": "-", "kind": KIND, "error": str(exc) or type(exc).__name__}], None
    for part in parts:
        try:
            fetched = await client.fetch_part(part, as_of)
        except (ECFRError, httpx.HTTPError) as exc:
            msg = str(exc) or type(exc).__name__
            errors.append({"code": part, "kind": KIND, "error": msg})
            if "rate limit" in msg:
                return changes, errors, as_of
            continue
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
        for key, old in snapshot.items():                 # pehle approved tha, ab eCFR mein nahi
            if old["kind"] != KIND or old["code"] != part or key in now_keys:
                continue
            h = "removed-" + old["hash"]
            if (key, h) in ignored:
                continue
            changes.append({"key": key, "kind": KIND, "code": part, "data": old["data"], "hash": h,
                            "change": "removed", "old": old["data"], "fields": []})
        if client.delay:
            await asyncio.sleep(client.delay)
    return changes, errors, as_of


def summarize(changes: list[dict], as_of: str | None = None) -> dict:
    by_code: dict[str, dict] = {}
    for c in changes:
        b = by_code.setdefault(f"21 CFR {c['code']}", {"new": 0, "changed": 0, "removed": 0})
        b[c["change"]] += 1
    return {"total": len(changes), "new": sum(1 for c in changes if c["change"] == "new"),
            "changed": sum(1 for c in changes if c["change"] == "changed"),
            "removed": sum(1 for c in changes if c["change"] == "removed"),
            "by_code": by_code, "as_of": as_of}
