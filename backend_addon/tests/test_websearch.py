import asyncio

import httpx

from app.services.websearch import WebSearch, build_web_context, sanitize_query, to_sources


def run(coro):
    return asyncio.run(coro)


def fake_ddg(rows):
    calls = []

    def f(q, n):
        calls.append(q)
        return rows

    f.calls = calls
    return f


ROWS = [
    {"title": "Steel <b>420</b>", "href": "https://www.example.com/steel", "body": "AISI 420 is a martensitic   stainless steel."},
    {"title": "Bad", "href": "javascript:alert(1)", "body": "x"},
    {"title": "Blocked", "href": "https://spam.test/a", "body": "x"},
    {"title": "Second", "href": "http://other.org/p", "body": "Another page"},
]


def test_sanitize_removes_private_bits():
    q = sanitize_query("price for ali@acme.com see https://acme.com/x call 0300-1234567 about mayo scissors")
    assert "@" not in q and "http" not in q and "1234567" not in q
    assert "mayo scissors" in q


def test_ddg_results_cleaned_filtered_and_limited():
    ws = WebSearch(ddg=fake_ddg(ROWS), blocked_domains=("spam.test",), max_results=4)
    out = run(ws.search("what is aisi 420 steel"))
    assert out.provider == "duckduckgo"
    assert [r.url for r in out.results] == ["https://www.example.com/steel", "http://other.org/p"]
    assert out.results[0].title == "Steel 420"
    assert out.results[0].snippet == "AISI 420 is a martensitic stainless steel."
    assert out.results[0].domain == "example.com"


def test_cache_avoids_second_call():
    f = fake_ddg(ROWS)
    ws = WebSearch(ddg=f)
    run(ws.search("what is aisi 420 steel"))
    run(ws.search("What is AISI 420 steel"))
    assert len(f.calls) == 1


def test_falls_back_to_wikipedia_when_ddg_fails():
    def boom(q, n):
        raise RuntimeError("rate limited")

    def handler(request):
        assert request.url.host == "en.wikipedia.org"
        return httpx.Response(200, json={"query": {"pages": {
            "2": {"title": "Second page", "index": 2, "extract": "Two."},
            "1": {"title": "Stainless steel", "index": 1, "extract": "Stainless steel is an alloy of iron."},
        }}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await WebSearch(ddg=boom, client=c).search("stainless steel")

    out = run(go())
    assert out.provider == "wikipedia"
    assert out.results[0].title == "Stainless steel"
    assert out.results[0].url == "https://en.wikipedia.org/wiki/Stainless_steel"
    assert "duckduckgo: RuntimeError" in out.error


def test_never_raises_when_everything_fails():
    def boom(q, n):
        raise RuntimeError("down")

    def handler(request):
        return httpx.Response(500)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await WebSearch(ddg=boom, client=c).search("anything at all")

    out = run(go())
    assert out.results == [] and out.provider == "" and out.error


def test_short_query_not_sent():
    f = fake_ddg(ROWS)
    out = run(WebSearch(ddg=f).search("ab 0300-1234567"))
    assert out.results == [] and not f.calls


def test_context_and_sources_shape():
    out = run(WebSearch(ddg=fake_ddg(ROWS), blocked_domains=("spam.test",)).search("aisi 420 steel info"))
    ctx = build_web_context(out.results)
    assert ctx.startswith("[1] Steel 420 (example.com)")
    src = to_sources(out.results)
    assert src[0]["kind"] == "web" and src[0]["ref"] == 1 and src[0]["url"].startswith("https://")


def test_check_host_reports_dns_vs_http_vs_ok():
    from app.services.websearch import check_host

    def bad_dns(h):
        raise OSError("Query Refused")

    assert check_host("x.com", resolve=bad_dns)[0] == "DNS"
    assert check_host("x.com", resolve=lambda h: [1], get=lambda h: 200)[0] == "OK"
    assert check_host("x.com", resolve=lambda h: [1], get=lambda h: 403)[0] == "HTTP"

    def refuse(h):
        raise ConnectionError("x")

    assert check_host("x.com", resolve=lambda h: [1], get=refuse)[0] == "NET"


def test_user_agent_carries_contact_and_backends_default():
    ws = WebSearch(contact="admin@mycompany.com", backends="bing,yahoo")
    assert "admin@mycompany.com" in ws.user_agent
    assert ws.backends == "bing,yahoo"


def test_ai_generated_encyclopedia_blocked_by_default():
    rows = [{"title": "A", "href": "https://grokipedia.com/page/a", "body": "x"}, {"title": "B", "href": "https://en.wikipedia.org/wiki/B", "body": "y"}]
    out = run(WebSearch(ddg=fake_ddg(rows)).search("some question here"))
    assert [r.domain for r in out.results] == ["en.wikipedia.org"]
