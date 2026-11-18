"""
EU law (EUR-Lex / CELLAR, Publications Office of the EU) live sync with admin approval. eCFR jaisa hi flow:

  fetch -> article-by-article hash compare (approved snapshot se) -> PENDING update -> super admin approve
  -> tab hi reg__eu mein ingest.

Free, koi API key nahi.
  * Latest CONSOLIDATED version (amendments samet) ka CELEX CELLAR SPARQL endpoint se: 32017R0745 -> 02017R0745-<date>.
  * Text: GET https://publications.europa.eu/resource/celex/<CELEX> (Accept: application/xhtml+xml, Accept-Language: eng).
Har Article / Annex ek record hai (recitals "Preamble" mein), is liye review mein pata chalta hai ke kaun sa hissa naya / badla / hata.

Tracked code: CELEX number. "32017R0745" (base act: hum latest consolidated khud dhoondte hain) ya
"02017R0745-20250110" (ek khaas consolidated version, jo khud update nahi hoga).
NOTE: consolidated text sirf jankari ke liye hai; asli (authentic) text Official Journal ka hai. Header mein yeh likha jata hai.
"""
import logging
import re
from html.parser import HTMLParser

import httpx

from app.services.lawsync import (USER_AGENT, LawSourceError, make_records, natural_key, norm_ws, render_records,
                                  today_utc, unique_key)

logger = logging.getLogger("company_ai.eurlex")

KIND = "eurlex"
COLLECTION = "reg__eu"
SOURCE_HOST = "eur-lex.europa.eu"
CELEX_RE = re.compile(r"^[03]\d{4}[A-Z]\d{4}(?:-\d{8})?$")
_URL_CELEX = re.compile(r"CELEX(?::|%3A)([0-9A-Za-z\-]+)", re.I)
DEFAULT_ITEMS = [("32017R0745", "EU MDR - Medical Device Regulation (EU) 2017/745")]
_MAX_BYTES = 40 * 1024 * 1024
_MIN_TEXT_CHARS = 2000                   # is se kam text = error / bot-check page ka shak

_ART = re.compile(r"^Article\s+(\d+[a-z]{0,2})$", re.I)
_ANNEX = re.compile(r"^ANNEX\s+([IVXLCDM]+[A-Z]?)$")
_MARK = re.compile(r"[\u25ba\u25bc\u25c4\u25b2]\s*[A-Z]\d{0,3}")        # consolidated text ke amendment nishan (M1, B ...)
_BRACKETS = re.compile(r"[\[\]]")
_TYPE_WORDS = {"R": "Reg", "L": "Dir", "D": "Dec", "H": "Rec"}
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def normalize_code(raw: str) -> str:
    """CELEX (ya EUR-Lex link jis mein CELEX:... ho) -> pakka CELEX."""
    raw = (raw or "").strip()
    m = _URL_CELEX.search(raw)
    code = (m.group(1) if m else raw).upper()
    if not CELEX_RE.match(code):
        raise LawSourceError("EU code is a CELEX number, for example 32017R0745 (or a consolidated one like 02017R0745-20250110)")
    return code


def act_id(code: str) -> str:
    """Base act ka CELEX (consolidated ho to bhi): 02017R0745-20250110 -> 32017R0745."""
    return "3" + code[1:10]


def item_name(code: str) -> str:
    m = re.match(r"^\d(\d{4})([A-Z])(\d{4})", code)
    if not m:
        return f"EU {code}"
    return f"EU {_TYPE_WORDS.get(m.group(2), m.group(2))} {m.group(1)}/{int(m.group(3))}"


def doc_id_for(code: str) -> str:
    return "eu-" + act_id(code).lower()


def _consolidated_date(celex: str) -> str | None:
    m = re.search(r"-(\d{4})(\d{2})(\d{2})$", celex)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


# ------------------------------------------------------------------ XHTML -> blocks
class _BlockParser(HTMLParser):
    """EUR-Lex XHTML: har <p> / <td> ek block (class + text). Ek hi <tr> ke blocks baad mein ek line bante hain."""
    _BLOCK = frozenset({"p", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "li"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str, int | None]] = []
        self._cls = ""
        self._buf: list[str] = []
        self._open = False
        self._skip = 0
        self._row = 0
        self._in_row = False

    def flush(self):
        if self._open:
            text = "".join(self._buf)
            if text.strip():
                self.blocks.append((self._cls, text, self._row if self._in_row else None))
        self._buf, self._open = [], False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "tr":
            self.flush()
            self._row += 1
            self._in_row = True
        elif tag in self._BLOCK:
            self.flush()
            self._open = True
            self._cls = dict(attrs).get("class") or ""
        elif tag == "br" and self._open:
            self._buf.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCK:
            self.flush()
        elif tag == "tr":
            self.flush()
            self._in_row = False

    def handle_data(self, data):
        if self._skip == 0 and self._open:
            self._buf.append(data)


def _dropped(cls: str) -> bool:
    """Footnotes aur amendment-marker blocks qanoon ka text nahi."""
    return any(t == "note" or t.endswith("-note") or t.endswith("modref") for t in cls.lower().split())


def _blocks(html: str) -> list[tuple[str, str]]:
    parser = _BlockParser()
    parser.feed(html)
    parser.close()
    parser.flush()
    merged: list[list] = []
    for cls, text, row in parser.blocks:
        if _dropped(cls):
            continue
        if merged and row is not None and merged[-1][2] == row:
            merged[-1][1] += " " + text
        else:
            merged.append([cls, text, row])
    out = []
    for cls, text, _ in merged:
        t = norm_ws(_MARK.sub("", text))
        if t:
            out.append((cls.lower(), t))
    return out


def _roman_value(s: str) -> int:
    s = re.sub(r"[^IVXLCDM]+$", "", s.upper())                # IIA -> II (aakhri harf ka suffix)
    total = 0
    for i, ch in enumerate(s):
        v = _ROMAN.get(ch, 0)
        total += -v if i + 1 < len(s) and _ROMAN.get(s[i + 1], 0) > v else v
    return total


def parse_xhtml(html: str, code: str) -> tuple[list[dict], dict]:
    """EUR-Lex XHTML -> ([{key, kind, code, data, hash}], meta). Bohat kam text mile to error."""
    blocks = _blocks(html)
    total = sum(len(t) for _, t in blocks)
    if total < _MIN_TEXT_CHARS:
        raise LawSourceError("EUR-Lex returned very little text (an error or bot-check page?); nothing was proposed")
    act = act_id(code)
    base = f"{KIND}:{act}"

    title_parts = [t for c, t in blocks[:12] if "doc-ti" in c][:3]
    meta = {"title": " ".join(title_parts)[:250]}

    records: list[dict] = []
    seen: set[str] = set()
    cur = {"kind": "preamble", "section": "Preamble", "key": f"{base}-preamble", "heading": "", "context": "", "lines": []}
    context, pending_ctx = "", []
    found_structure = False

    def close():
        text = "\n".join(cur["lines"])
        if text or cur["heading"]:
            data = {"act": act, "section": cur["section"], "heading": cur["heading"][:300], "context": cur["context"][:200]}
            records.extend(make_records(KIND, code, unique_key(seen, cur["key"]), data, text))

    for cls, t in blocks:
        art, annex = _ART.match(t), _ANNEX.match(t)
        if art:
            close()
            found_structure = True
            n = art.group(1)
            cur = {"kind": "article", "section": f"Art {n}", "key": f"{base}-art-{n.lower()}", "heading": "",
                   "context": context, "lines": []}
        elif annex:
            close()
            found_structure = True
            r = annex.group(1)
            cur = {"kind": "annex", "section": f"Annex {r}", "key": f"{base}-annex-{r.lower()}", "heading": "",
                   "context": "", "lines": []}
        elif cur["kind"] == "article" and cls.endswith("sti-art") and not cur["heading"] and not cur["lines"]:
            cur["heading"] = t
        elif (cur["kind"] == "annex" and not cur["heading"] and not cur["lines"] and len(t) <= 250
              and any(w in cls for w in ("grseq", "annex", "doc-ti", "title"))):
            cur["heading"] = t
        elif cls.endswith("ti-section-1") and cur["kind"] != "annex":
            pending_ctx = [t]
            context = t
        elif cls.endswith("ti-section-2") and cur["kind"] != "annex" and pending_ctx:
            pending_ctx.append(t)
            context = " - ".join(pending_ctx)
        else:
            cur["lines"].append(t)
    close()

    if not found_structure:                                   # Article/Annex pehchane nahi gaye: poora text (review mein nazar aayega)
        records = make_records(KIND, code, f"{base}-full",
                               {"act": act, "section": "Full text", "heading": "", "context": ""},
                               "\n".join(t for _, t in blocks))
        meta["parsed"] = False
    return records, meta


# ------------------------------------------------------------------ client
class EurLexClient:

    def __init__(self, base_url: str = "https://publications.europa.eu",
                 sparql_url: str = "https://publications.europa.eu/webapi/rdf/sparql", timeout: float = 90.0,
                 transport: httpx.AsyncBaseTransport | None = None, delay: float = 1.0, language: str = "eng"):
        self.base_url = base_url.rstrip("/")
        self.sparql_url = sparql_url
        self.timeout = timeout
        self.transport = transport
        self.delay = delay
        self.language = language

    def _http(self, **headers):
        return httpx.AsyncClient(timeout=self.timeout, transport=self.transport, follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT, **headers})

    async def latest_consolidated(self, code: str) -> str | None:
        """Base act ka sabse naya consolidated CELEX (jaise 02017R0745-20250110). Koi consolidated na ho to None.
        Network / server error par LawSourceError (kabhi chupke se purane text par nahi girta)."""
        prefix = "0" + act_id(code)[1:] + "-"                  # code validate ho chuka: query mein sirf [0-9A-Z-]
        query = ("PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
                 "SELECT ?celex WHERE {\n  ?work cdm:resource_legal_id_celex ?celex .\n"
                 f"  FILTER(STRSTARTS(STR(?celex), \"{prefix}\"))\n"
                 "}\nORDER BY DESC(?celex)\nLIMIT 1")
        async with self._http(Accept="application/sparql-results+json") as http:
            res = await http.post(self.sparql_url, data={"query": query})
        if res.status_code == 429:
            raise LawSourceError("EUR-Lex rate limit reached; try again later")
        if res.status_code >= 400:
            raise LawSourceError(f"EUR-Lex SPARQL returned HTTP {res.status_code}; enter the consolidated CELEX by hand "
                                 f"(for example {prefix}20250110, see 'Consolidated text' on eur-lex.europa.eu)")
        try:
            rows = res.json()["results"]["bindings"]
        except (ValueError, KeyError, TypeError):
            raise LawSourceError("EUR-Lex SPARQL returned an unreadable response")
        if not rows:
            return None
        celex = str(rows[0].get("celex", {}).get("value", "")).upper()
        if not CELEX_RE.match(celex) or not celex.startswith(prefix):
            raise LawSourceError("EUR-Lex SPARQL returned an unexpected CELEX value")
        return celex

    async def fetch_act(self, code: str) -> tuple[list[dict], dict]:
        code = normalize_code(code)
        note = None
        if _consolidated_date(code):
            celex = code                                         # admin ne khaas version diya
        else:
            celex = await self.latest_consolidated(code)
            if celex is None:
                if code.startswith("0"):
                    raise LawSourceError(f"no consolidated version found for {code}")
                celex = code
                note = "No consolidated version exists; this is the text as originally published in the Official Journal."
        async with self._http(Accept="application/xhtml+xml, text/html;q=0.9", **{"Accept-Language": self.language}) as http:
            res = await http.get(f"{self.base_url}/resource/celex/{celex}")
        if res.status_code == 404:
            raise LawSourceError(f"EUR-Lex has no document {celex} in this language (HTTP 404)")
        if res.status_code == 429:
            raise LawSourceError("EUR-Lex rate limit reached; try again later")
        if res.status_code >= 400:
            raise LawSourceError(f"EUR-Lex returned HTTP {res.status_code}")
        if len(res.content) > _MAX_BYTES:
            raise LawSourceError("EUR-Lex response is too large")
        ctype = str(res.headers.get("content-type", "")).lower()
        if ctype and "html" not in ctype and "xml" not in ctype:
            raise LawSourceError(f"EUR-Lex returned an unexpected content type ({ctype.split(';')[0]})")
        records, meta = parse_xhtml(res.text, code)
        retrieved = today_utc()
        meta.update({"celex": celex, "retrieved": retrieved, "as_of": _consolidated_date(celex) or retrieved,
                     "consolidated": celex.startswith("0"), "note": note,
                     "source_url": f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex}"})
        return records, meta


def _order(key: str):
    k = key.lower()
    if k.endswith("-preamble"):
        return 0, 0, []
    if "-annex-" in k:
        roman = k.split("-annex-", 1)[1].split("-p")[0]
        return 2, _roman_value(roman), natural_key(k)
    return 1, 0, natural_key(k)


def render(code: str, label: str | None, records: list[dict], meta: dict | None) -> str:
    """Approved articles -> ek document ka plain text (RAG ke liye). Har paragraph par [EU Reg 2017/745 Art 10] tag."""
    meta = meta or {}
    prefix = item_name(code)
    celex = meta.get("celex") or code
    title = label or meta.get("title") or code
    if meta.get("consolidated", celex.startswith("0")):
        version = f"consolidated version of {meta.get('as_of')} (informational; the Official Journal text is authentic)"
    else:
        version = f"original Official Journal text, retrieved {meta.get('retrieved') or meta.get('as_of')}"
    note = f" {meta['note']}" if meta.get("note") else ""
    head = (f"EU legislation: {title} (CELEX {celex}). Source: EUR-Lex, Publications Office of the European Union "
            f"(eur-lex.europa.eu), {version}, approved by an administrator.{note}")

    def tag_for(d: dict) -> str:
        return f"{prefix} {_BRACKETS.sub('', d.get('section') or '')}".strip()

    return "\n".join([head, ""] + render_records(tag_for, records, _order)).strip()
