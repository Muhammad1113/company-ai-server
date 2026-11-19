"""
UK legislation (legislation.gov.uk, The National Archives) live sync with admin approval. eCFR jaisa hi flow:

  fetch -> section-by-section hash compare (approved snapshot se) -> PENDING update -> super admin approve
  -> tab hi reg__uk mein ingest.

Free, koi API key nahi, Open Government Licence v3.0. Kisi bhi act/SI ke URL ke aage /data.xml lagao to CLML XML milta hai
(https://legislation.github.io/data-documentation/api/overview.html). Har provision (section / regulation / schedule paragraph)
ek record hai, is liye review mein pata chalta hai ke kaun sa hissa naya / badla / hata.

Tracked code: "<type>/<year>/<number>", jaise uksi/2002/618 (UK Medical Devices Regulations 2002).
"""
import logging
import re
import xml.etree.ElementTree as ET

import httpx

from app.services.lawsync import (USER_AGENT, LawSourceError, make_records, natural_key, norm_ws, reject_entities,
                                  render_records, today_utc, unique_key)

logger = logging.getLogger("company_ai.uklegislation")

KIND = "ukleg"
COLLECTION = "reg__uk"
SOURCE_HOST = "legislation.gov.uk"
CODE_RE = re.compile(r"^[a-z]{2,8}/\d{4}/\d{1,4}$")
_URL_RE = re.compile(r"legislation\.gov\.uk/(?:id/)?([a-z]{2,8}/\d{4}/\d{1,4})", re.I)
DEFAULT_ITEMS = [("uksi/2002/618", "Medical Devices Regulations 2002 (UK MDR 2002)")]
_MAX_BYTES = 40 * 1024 * 1024
_BRACKETS = re.compile(r"[\[\]]")

_TYPE_LABELS = {"uksi": "SI", "ukpga": "Act", "ukla": "Local Act", "asp": "ASP", "ssi": "SSI", "wsi": "WSI",
                "anaw": "Act Wales", "asc": "Act Wales", "nia": "Act NI", "nisr": "SR NI", "eur": "EU retained",
                "eudn": "EU retained", "eudr": "EU retained", "ukcm": "Measure", "mwa": "Measure Wales"}
_ID_WORDS = {"section": "s", "regulation": "reg", "article": "art", "rule": "r", "schedule": "Sch", "paragraph": "para",
             "part": "Pt", "chapter": "ch", "annex": "Annex", "appendix": "App", "order": "art"}

# CLML ke woh hisse jin ka text qanoon ka hissa nahi (footnotes, metadata, repealed words ke nishaan)
_SKIP = frozenset({"Repeal", "CommentaryRef", "FootnoteRef", "Commentaries", "Commentary", "Footnotes", "Footnote",
                   "Metadata", "Contents", "ExplanatoryNotes", "EarlierOrders"})
_BREAK = frozenset({"P1", "P2", "P3", "P4", "P5", "P6", "P7", "ListItem", "tr", "Para", "BlockText", "Title", "Number"})
_STRUCT = frozenset({"Part", "Chapter", "Pblock", "PsubBlock", "Schedule", "EUPart", "EUChapter", "EUTitle", "EUSection",
                     "EUSubsection", "Division", "Appendix"})


def normalize_code(raw: str) -> str:
    """'uksi/2002/618', ya poora legislation.gov.uk link -> 'uksi/2002/618'."""
    raw = (raw or "").strip()
    m = _URL_RE.search(raw)
    code = (m.group(1) if m else raw).lower().strip("/")
    if not CODE_RE.match(code):
        raise LawSourceError("UK legislation code looks like uksi/2002/618 (type/year/number), or paste the legislation.gov.uk link")
    return code


def item_name(code: str) -> str:
    typ, year, num = code.split("/")
    return f"UK {_TYPE_LABELS.get(typ, typ.upper())} {year}/{num}"


def doc_id_for(code: str) -> str:
    return "uk-" + code.replace("/", "-")


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _text(el, skip_root_number: bool = False) -> str:
    """CLML element -> plain text, har sub-paragraph nayi line par. Repealed words / footnote nishan chhod deta hai."""
    out: list[str] = []

    def walk(e, depth: int):
        name = _local(e.tag)
        if name in _SKIP:
            return
        if depth and name in _BREAK:
            out.append("\n")
        if name == "Pnumber":
            if not (skip_root_number and depth == 1):
                num = norm_ws("".join(e.itertext()))
                if num:
                    out.append(f"{e.get('PuncBefore', '')}{num}{e.get('PuncAfter', '')} ")
            return
        if e.text:
            out.append(e.text)
        for c in e:
            walk(c, depth + 1)
            if c.tail:
                out.append(c.tail)
        if name in ("td", "th"):
            out.append(" | ")

    walk(el, 0)
    lines = []
    for ln in "".join(out).split("\n"):
        ln = re.sub(r"\s*\|\s*$", "", norm_ws(ln))
        if ln:
            lines.append(ln)
    return "\n".join(lines)


def _flat(el) -> str:
    return norm_ws(_text(el).replace("\n", " "))


def _label_of(el) -> str:
    num = title = ""
    for c in el:
        n = _local(c.tag)
        if n == "Number" and not num:
            num = _flat(c)
        elif n == "Title" and not title:
            title = _flat(c)
        elif n == "TitleBlock" and not title:
            for t in c:
                if _local(t.tag) == "Title":
                    title = _flat(t)
                    break
    return " - ".join(x for x in (num, title) if x)


def _label_from_id(pid: str) -> str:
    """'regulation-5' -> 'reg 5'; 'schedule-1-paragraph-3' -> 'Sch 1 para 3'."""
    toks = [t for t in (pid or "").split("-") if t]
    out, i = [], 0
    while i < len(toks):
        word = toks[i].lower()
        if word in _ID_WORDS and i + 1 < len(toks):
            out.append(f"{_ID_WORDS[word]} {toks[i + 1]}")
            i += 2
        else:
            out.append(toks[i])
            i += 1
    return " ".join(out)


def _collect(node, ctx: str, out: list):
    """Provisions (P1) jama karo. P1 ke andar nahi jate (uska poora text ek record hai)."""
    for child in node:
        n = _local(child.tag)
        if n in _SKIP:
            continue
        if n == "P1group":
            title = ""
            for c in child:
                if _local(c.tag) == "Title":
                    title = _flat(c)
                    break
            for c in child:
                if _local(c.tag) == "P1":
                    out.append((c, title, ctx, False))
        elif n == "P1":
            out.append((child, "", ctx, False))
        elif n in _STRUCT:
            label = _label_of(child)
            new_ctx = f"{ctx}; {label}" if ctx and label else (label or ctx)
            before = len(out)
            _collect(child, new_ctx[:200], out)
            if n == "Schedule" and len(out) == before:          # schedule mein P1 nahi: poora schedule ek record
                out.append((child, label, ctx, True))
        else:
            _collect(child, ctx, out)


def _metadata(root) -> dict:
    title = modified = ""
    for el in root.iter():
        if _local(el.tag) == "Metadata":
            for m in el.iter():
                ln = _local(m.tag)
                if ln == "title" and not title:
                    title = norm_ws("".join(m.itertext()))
                elif ln == "modified" and not modified:
                    modified = norm_ws("".join(m.itertext()))
            break
    return {"title": title[:200], "modified": modified[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", modified) else None}


def parse_clml(xml_bytes: bytes, code: str) -> tuple[list[dict], dict]:
    """legislation.gov.uk CLML -> ([{key, kind, code, data, hash}], meta). Koi section na mile to error (khali jawab approve na ho)."""
    reject_entities(xml_bytes)
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise LawSourceError(f"legislation.gov.uk returned an unreadable response ({exc})")
    meta = _metadata(root)
    typ, year, num = code.split("/")
    base = f"{KIND}:{typ}-{year}-{num}"
    wrappers = [c for c in root if _local(c.tag) in ("Primary", "Secondary", "EURetained")] or [root]

    records: list[dict] = []
    seen: set[str] = set()

    prelim_texts = [_text(c) for w in wrappers for c in w if _local(c.tag).endswith("Prelims")]
    prelim = "\n".join(t for t in prelim_texts if t)
    if prelim:
        records += make_records(KIND, code, unique_key(seen, f"{base}-preamble"),
                                {"act": code, "section": "Preamble", "heading": "", "context": ""}, prelim)

    provisions: list = []
    for w in wrappers:
        _collect(w, "", provisions)
    for i, (el, title, ctx, whole) in enumerate(provisions, 1):
        pid = el.get("id") or ""
        if not pid:
            number = next((norm_ws("".join(c.itertext())) for c in el if _local(c.tag) == "Pnumber"), "")
            pid = f"p-{number}" if number else f"p{i}"
        slug = re.sub(r"[^A-Za-z0-9.\-]+", "-", pid).strip("-").lower()[:60] or f"p{i}"
        text = _text(el, skip_root_number=not whole)
        data = {"act": code, "section": _label_from_id(pid)[:60], "heading": title[:300], "context": ctx[:200]}
        records += make_records(KIND, code, unique_key(seen, f"{base}-{slug}"), data, text)

    if not provisions:                                    # structure samajh nahi aaya: poora text (review mein nazar aayega)
        body = "\n".join(t for t in (_text(w) for w in wrappers) if t)
        if not body.strip():
            raise LawSourceError(f"{code} had no readable text in the legislation.gov.uk response")
        records += make_records(KIND, code, unique_key(seen, f"{base}-full"),
                                {"act": code, "section": "Full text", "heading": "", "context": ""}, body)
        meta["parsed"] = False
    return records, meta


class UKLegislationClient:

    def __init__(self, base_url: str = "https://www.legislation.gov.uk", timeout: float = 60.0,
                 transport: httpx.AsyncBaseTransport | None = None, delay: float = 1.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport
        self.delay = delay

    async def fetch_act(self, code: str) -> tuple[list[dict], dict]:
        code = normalize_code(code)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/xml, text/xml;q=0.9"}
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport, follow_redirects=True,
                                     headers=headers) as http:
            res = await http.get(f"{self.base_url}/{code}/data.xml")
        if res.status_code == 404:
            raise LawSourceError(f"legislation.gov.uk has no item {code} (HTTP 404)")
        if res.status_code == 429:
            raise LawSourceError("legislation.gov.uk rate limit reached; try again later")
        if res.status_code >= 400:
            raise LawSourceError(f"legislation.gov.uk returned HTTP {res.status_code}")
        if len(res.content) > _MAX_BYTES:
            raise LawSourceError("legislation.gov.uk response is too large")
        records, meta = parse_clml(res.content, code)
        retrieved = today_utc()
        meta.update({"retrieved": retrieved, "as_of": retrieved, "source_url": f"{self.base_url}/{code}"})
        return records, meta


def _order(key: str):
    k = key.lower()
    rank = 0 if k.endswith("-preamble") else 2 if "-schedule-" in k else 1       # preamble, body, phir schedules
    return rank, natural_key(key)


def render(code: str, label: str | None, records: list[dict], meta: dict | None) -> str:
    """Approved provisions -> ek document ka plain text (RAG ke liye). Har paragraph par [UK SI 2002/618 reg 5] tag."""
    meta = meta or {}
    prefix = item_name(code)
    title = meta.get("title") or label or code
    when = meta.get("retrieved") or meta.get("as_of")
    modified = f" (source last modified {meta['modified']})" if meta.get("modified") else ""
    head = (f"UK legislation: {title} ({code}). Source: legislation.gov.uk (The National Archives, Open Government Licence v3.0), "
            f"text retrieved {when}{modified}, approved by an administrator. It may not include very recent amendments; "
            "legislation.gov.uk is the authority.")

    def tag_for(d: dict) -> str:
        return f"{prefix} {_BRACKETS.sub('', d.get('section') or '')}".strip()

    return "\n".join([head, ""] + render_records(tag_for, records, _order)).strip()
