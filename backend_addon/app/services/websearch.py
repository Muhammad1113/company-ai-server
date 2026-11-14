"""Free internet search for the chatbot (no API key, no Docker).

Primary:  DuckDuckGo through the `ddgs` package (unofficial, can be rate limited).
Fallback: Wikipedia API (official and free), used when DuckDuckGo fails or returns nothing.

Design rules (anti-hallucination, privacy):
- search() never raises. On any failure it returns an empty list and says why in `Outcome.error`.
- The query that leaves the server is sanitized first (emails, urls, long numbers removed).
- Results are short (title, url, snippet) so they fit the 4096-token context of the model.
- Only http/https urls are returned.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import quote, urlparse

import httpx

log = logging.getLogger("websearch")

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_EMAIL = re.compile(r"\S+@\S+\.\S+")
_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_LONGNUM = re.compile(r"\b\d[\d\-\s]{7,}\d\b")


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    snippet: str

    @property
    def domain(self) -> str:
        return (urlparse(self.url).hostname or "").removeprefix("www.")


@dataclass
class Outcome:
    results: list[WebResult] = field(default_factory=list)
    provider: str = ""
    error: str = ""
    query: str = ""          # the sanitized query that was actually sent


def sanitize_query(q: str, max_chars: int = 200) -> str:
    """Remove things that must not leave the company: emails, urls, long digit strings (phones, accounts)."""
    q = _URL.sub(" ", q or "")
    q = _EMAIL.sub(" ", q)
    q = _LONGNUM.sub(" ", q)
    q = _WS.sub(" ", q).strip()
    return q[:max_chars].strip()


def _clean(text: str, limit: int) -> str:
    text = html.unescape(_TAG.sub(" ", text or ""))
    text = _WS.sub(" ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _ok_url(url: str) -> bool:
    try:
        u = urlparse(url)
        return u.scheme in ("http", "https") and bool(u.hostname)
    except ValueError:
        return False


def _ddg_search(query: str, n: int, backends: str = "auto", timeout: float = 8.0) -> list[dict]:
    from ddgs import DDGS  # lazy: only needed when internet search is enabled

    # backends: "auto" or comma list like "duckduckgo,bing,yahoo,mojeek,startpage"
    return list(DDGS(timeout=max(3, int(timeout))).text(query, max_results=n, backend=backends))


class WebSearch:
    def __init__(
        self,
        *,
        max_results: int = 4,
        timeout: float = 8.0,
        cache_ttl: float = 900.0,
        snippet_chars: int = 300,
        blocked_domains: tuple[str, ...] = ("grokipedia.com",),  # AI-generated pages: not a reliable source
        wikipedia_lang: str = "en",
        backends: str = "auto",
        contact: str = "",
        ddg: Callable[[str, int], list[dict]] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.max_results = max_results
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.snippet_chars = snippet_chars
        self.blocked = tuple(d.lower() for d in blocked_domains)
        self.lang = wikipedia_lang
        self.backends = backends or "auto"
        # Wikimedia blocks (403) clients without a real contact in the User-Agent: set WEB_SEARCH_CONTACT to an admin email
        self.user_agent = f"CompanyAIBot/1.0 ({contact or 'internal-assistant'}) python-httpx"
        self._ddg = ddg or (lambda q, n: _ddg_search(q, n, self.backends, self.timeout))
        self._client = client
        self._cache: dict[str, tuple[float, Outcome]] = {}

    # ---------- providers ----------
    async def _from_ddg(self, q: str) -> list[WebResult]:
        rows = await asyncio.wait_for(asyncio.to_thread(self._ddg, q, self.max_results * 2), self.timeout)
        out: list[WebResult] = []
        for r in rows or []:
            url = str(r.get("href") or r.get("url") or "")
            if _ok_url(url):
                out.append(WebResult(_clean(str(r.get("title", "")), 120), url, _clean(str(r.get("body", "")), self.snippet_chars)))
        return out

    async def _from_wikipedia(self, q: str) -> list[WebResult]:
        params = {
            "action": "query", "format": "json", "utf8": 1, "generator": "search", "gsrsearch": q,
            "gsrlimit": self.max_results, "prop": "extracts", "exintro": 1, "explaintext": 1, "exsentences": 3,
        }
        url = f"https://{self.lang}.wikipedia.org/w/api.php"
        client = self._client or httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": self.user_agent, "Api-User-Agent": self.user_agent})
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            pages = list((resp.json().get("query", {}).get("pages", {})).values())
        finally:
            if self._client is None:
                await client.aclose()
        pages.sort(key=lambda p: p.get("index", 99))
        out = []
        for p in pages:
            title = str(p.get("title", ""))
            extract = _clean(str(p.get("extract", "")), self.snippet_chars)
            if title and extract:
                out.append(WebResult(title, f"https://{self.lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}", extract))
        return out

    # ---------- public ----------
    async def search(self, query: str) -> Outcome:
        q = sanitize_query(query)
        if len(q) < 3:
            return Outcome(query=q, error="query too short after cleaning")
        hit = self._cache.get(q.lower())
        if hit and time.monotonic() - hit[0] < self.cache_ttl:
            return hit[1]

        errors: list[str] = []
        for name, fn in (("duckduckgo", self._from_ddg), ("wikipedia", self._from_wikipedia)):
            try:
                results = await fn(q)
            except Exception as exc:  # network, rate limit, missing package, timeout
                errors.append(f"{name}: {type(exc).__name__}")
                log.warning("web search via %s failed: %s", name, exc)
                continue
            results = [r for r in results if not any(r.domain.endswith(b) for b in self.blocked)][: self.max_results]
            if results:
                outcome = Outcome(results=results, provider=name, query=q, error="; ".join(errors))
                self._cache[q.lower()] = (time.monotonic(), outcome)
                if len(self._cache) > 256:
                    self._cache.pop(next(iter(self._cache)))
                return outcome
            errors.append(f"{name}: no results")
        return Outcome(query=q, error="; ".join(errors) or "no results")


# ---------- prompt + source helpers (pure) ----------
WEB_RULES = (
    "You answer using ONLY the numbered web results below. "
    "Cite every fact with its number like [1]. "
    "If the results do not answer the question, reply exactly: I could not find a reliable answer on the internet. "
    "Never add facts, numbers, dates or names that are not in the results. "
    "Start your answer with: From the internet:"
)


def build_web_context(results: list[WebResult]) -> str:
    return "\n".join(f"[{i}] {r.title} ({r.domain})\n{r.snippet}" for i, r in enumerate(results, 1))


def to_sources(results: list[WebResult]) -> list[dict]:
    """Same shape as document sources, plus kind/url/title (the frontend shows them as website cards)."""
    return [
        {"ref": i, "kind": "web", "doc_id": r.domain, "title": r.title, "url": r.url, "preview": r.snippet, "chunk_index": 0, "score": None}
        for i, r in enumerate(results, 1)
    ]


# ---------- network diagnosis ----------
HOSTS = [
    ("duckduckgo", "duckduckgo.com"),
    ("bing", "www.bing.com"),
    ("yahoo", "search.yahoo.com"),
    ("mojeek", "www.mojeek.com"),
    ("startpage", "www.startpage.com"),
    ("google", "www.google.com"),
    ("brave", "search.brave.com"),
    ("yandex", "yandex.com"),
    ("wikipedia", "en.wikipedia.org"),
]


def check_host(host: str, resolve=None, get=None) -> tuple[str, str]:
    """Returns (status, detail). status: OK | DNS | HTTP | NET."""
    import socket

    resolve = resolve or (lambda h: socket.getaddrinfo(h, 443))
    get = get or (lambda h: httpx.get(f"https://{h}", timeout=6.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}).status_code)
    try:
        resolve(host)
    except Exception as exc:
        return "DNS", f"cannot resolve ({type(exc).__name__})"
    try:
        code = get(host)
    except Exception as exc:
        return "NET", f"resolves but connection fails ({type(exc).__name__})"
    return ("OK", f"HTTP {code}") if code < 400 else ("HTTP", f"HTTP {code}")


def diagnose() -> str:
    lines, ok = [], []
    for name, host in HOSTS:
        status, detail = check_host(host)
        lines.append(f"{name:<10} {host:<22} {status:<5} {detail}")
        if status == "OK" and name != "wikipedia":
            ok.append(name)
    if ok:
        lines.append(f"\nSet in .env:  WEB_SEARCH_BACKENDS={','.join(ok)}")
    else:
        lines.append("\nNo search engine is reachable from this machine. If you see DNS errors, change the DNS server of the network adapter to 1.1.1.1 / 8.8.8.8 and run this again.")
    return "\n".join(lines)


if __name__ == "__main__":  # python -m app.services.websearch "your question"   |   python -m app.services.websearch --diagnose
    import json
    import os
    import sys

    async def _main() -> None:
        ws = WebSearch(backends=os.getenv("WEB_SEARCH_BACKENDS", "auto"), contact=os.getenv("WEB_SEARCH_CONTACT", ""))
        out = await ws.search(" ".join(sys.argv[1:]) or "what is stainless steel 420")
        print(json.dumps({"provider": out.provider, "error": out.error, "query": out.query, "results": to_sources(out.results)}, indent=2, ensure_ascii=False))

    if "--diagnose" in sys.argv:
        print(diagnose())
    else:
        asyncio.run(_main())
