"""Phase 7: tools, router, websearch safety, agent. Network/GPU nahi chahiye (sab fake)."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.services.agent import Agent, with_clock
from app.services.router import needs_fresh_info, plan_route, build_web_query, chitchat_or_task
from app.services.tools import run_tools, safe_eval, CalcError
from app.services.websearch import WebSearch, WebResult, is_relevant, keywords, privacy_block_reason, sanitize_query

NOW = datetime(2026, 11, 11, 14, 5, tzinfo=timezone(timedelta(hours=5), "PKT"))


def tool_text(msg):
    return [t.summary for t in run_tools(msg, NOW)]


# ---------------------------------------------------------------- tools
def test_calculator_basic():
    assert tool_text("2+2") == ["2+2 = 4"]
    assert tool_text("calculate (12.5 * 8) / 4") == ["(12.5 * 8) / 4 = 25"]
    assert tool_text("what is 1,000 + 250?") == ["1000 + 250 = 1,250"]
    assert tool_text("15% of 200") == ["15% of 200 = 30"]
    assert tool_text("200 ka 15%") == ["200 ka 15% = 30"]
    assert tool_text("sqrt(144) + 3") == ["sqrt(144) + 3 = 15"]
    assert tool_text("2^10") == ["2^10 = 1,024"]


def test_calculator_not_triggered_on_plain_numbers_dates_phones():
    for msg in ["2024", "11-11-2026", "0300-1234567", "what is a hernia", "5-3", "hello 2 times"]:
        assert not any(t.name == "calculator" for t in run_tools(msg, NOW)), msg
    assert tool_text("calculate 5-3") == ["5-3 = 2"]


def test_calculator_is_safe():
    for bad in ["__import__('os').system('x')", "9**9**9", "1/0", "().__class__", "10**1000", "a+b", "[1]*10**9"]:
        with pytest.raises(CalcError):
            safe_eval(bad)
    assert not run_tools("calculate 9**9**9", NOW)
    assert not run_tools("calculate 1/0", NOW)


def test_unit_conversion():
    assert tool_text("convert 70 kg to lb") == ["70 kg = 154.3236 lb"]
    assert tool_text("5 kg ko lbs mein") == ["5 kg = 11.0231 lb"]
    assert tool_text("98.6 F to C") == ["98.6 °F = 37 °C"]
    assert tool_text("12 fr to mm") == ["12 Fr = 4 mm"]
    assert tool_text("120 mmhg to kpa") == ["120 mmHg = 15.9987 kPa"]
    assert not run_tools("5 kg to cm", NOW)          # alag categories


def test_bmi():
    out = tool_text("bmi for 70 kg and 175 cm")
    assert out and out[0].startswith("BMI = 22.9") and "normal" in out[0]
    assert not run_tools("bmi of my patient", NOW)


def test_datetime_tool():
    out = tool_text("aaj ki tarikh kya hai")
    assert out and "11 November 2026" in out[0] and "Wednesday" in out[0]
    assert tool_text("what's the time") and tool_text("kitne baje hain")
    assert not run_tools("founder of pakistan", NOW)


def test_with_clock_has_date():
    assert "Current local date and time" in with_clock("", "Asia/Karachi")
    assert with_clock("Be brief.", "Asia/Karachi").startswith("Be brief.")


# ---------------------------------------------------------------- router
def test_router_fresh_detection():
    for q in ["who is the current prime minister of pakistan", "latest news about karachi", "dollar rate today",
              "pm kaun hai pakistan ka", "who is the president of USA", "abhi weather kaisa hai",
              "search for stapler suture types"]:
        assert needs_fresh_info(q), q
    for q in ["who is the founder of pakistan", "who was the first prime minister of pakistan",
              "explain laparoscopic cholecystectomy steps", "what is a normal heart rate", "capital of france"]:
        assert not needs_fresh_info(q), q


def test_router_plan_priorities():
    assert plan_route("latest news", collection="hr").route == "rag"                 # docs kabhi internet par nahi
    assert plan_route("latest news", web_mode="on", collection="hr").route == "rag"
    t = run_tools("2+2", NOW)
    assert plan_route("2+2", tools=t).route == "tool"
    assert plan_route("latest news", web_mode="off").route == "direct"
    assert plan_route("latest news", web_available=False).route == "direct"
    assert plan_route("capital of france", web_mode="on").route == "web"
    assert plan_route("latest news").route == "web"
    assert plan_route("founder of pakistan", policy="fresh").route == "direct"


def test_follow_up_query_gets_context():
    assert build_web_query("aur Punjab ka?", "who is the chief minister of sindh") == "who is the chief minister of sindh aur Punjab ka"
    assert build_web_query("who is the current pm of pakistan", "old question") == "who is the current pm of pakistan"


# ---------------------------------------------------------------- privacy / sanitize
def test_privacy_gate():
    assert privacy_block_reason("latest guideline for my patient Ahmed")
    assert privacy_block_reason("mail me at a@b.com")
    assert privacy_block_reason("MRN 12345678 lab report")
    assert privacy_block_reason("cnic 35202-1234567-1")
    assert privacy_block_reason("meri mareeza ka case")
    assert privacy_block_reason("anything about Acme Corp", terms="acme corp")
    assert privacy_block_reason("who is the current prime minister of pakistan") == ""


def test_sanitize_query():
    assert sanitize_query("see https://x.com/a?b=1 now") == "see now"
    assert len(sanitize_query("a" * 500)) == 200


# ---------------------------------------------------------------- websearch
class FakeDDG:
    def __init__(self, rows=None, fail=False, delay=0.0):
        self.calls = 0
        self.rows = rows if rows is not None else [
            {"title": "Prime Minister of Pakistan", "href": "https://en.wikipedia.org/wiki/PM", "body": "<b>The PM</b> is X."},
            {"title": "bad", "href": "javascript:alert(1)", "body": "x"},
            {"title": "blocked", "href": "https://spam.example.com/a", "body": "y"},
        ]
        self.fail, self.delay = fail, delay

    def __call__(self, q, n, timeout=8.0, proxy=""):
        import time
        self.calls += 1
        time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("Ratelimit 202")
        return self.rows


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(ddg, **kw):
    kw.setdefault("providers", ("duckduckgo",))
    kw.setdefault("blocked_domains", ("spam.example.com",))
    return WebSearch(ddg=ddg, **kw)


def run(coro):
    return asyncio.run(coro)


def test_search_filters_and_cleans():
    ws = make(FakeDDG())
    out = run(ws.search("who is the current pm of pakistan", user="u1"))
    assert [r.title for r in out.results] == ["Prime Minister of Pakistan"]
    assert out.results[0].snippet == "The PM is X." and out.provider == "duckduckgo"
    ws.close()


def test_blocked_query_never_calls_provider():
    ddg = FakeDDG()
    ws = make(ddg)
    out = run(ws.search("latest treatment for my patient Ali", user="u1"))
    assert out.blocked and not out.results and ddg.calls == 0
    ws.close()


def test_cache_and_single_flight():
    ddg = FakeDDG(delay=0.2)
    ws = make(ddg, per_user_per_minute=0, global_per_minute=0)

    async def go():
        qs = ["Who is the current PM of Pakistan?", "who is the current pm of pakistan", "WHO IS THE CURRENT PM OF PAKISTAN!!"]
        res = await asyncio.gather(*[ws.search(q, user=f"u{i}") for i, q in enumerate(qs)])
        again = await ws.search("who is the current pm of pakistan", user="u9")
        return res, again

    res, again = run(go())
    assert ddg.calls == 1                                  # 3 users + cache = 1 outbound request
    assert all(r.results for r in res) and again.results
    ws.close()


def test_rate_limits_user_and_global():
    clock = Clock()
    ws = make(FakeDDG(), per_user_per_minute=2, global_per_minute=100, clock=clock)

    async def go():
        outs = [await ws.search(f"unique question number {i} here", user="u1") for i in range(4)]
        other = await ws.search("another unique question here", user="u2")
        return outs, other

    outs, other = run(go())
    assert [o.limited for o in outs[:2]] == [False, False]
    assert outs[3].limited and outs[3].retry_after > 0
    assert not other.limited                               # dusre user par asar nahi

    ws2 = make(FakeDDG(), per_user_per_minute=100, global_per_minute=2, clock=Clock())

    async def go2():
        return [await ws2.search(f"global question {i} abc", user=f"u{i}") for i in range(5)]

    outs2 = run(go2())
    assert sum(o.limited for o in outs2) >= 1
    ws.close(); ws2.close()


def test_circuit_breaker_stops_hammering_and_falls_back():
    clock = Clock()
    ddg = FakeDDG(fail=True)
    ws = make(ddg, providers=("duckduckgo",), per_user_per_minute=0, global_per_minute=0, clock=clock, cooldown_base=60)

    async def go():
        a = await ws.search("first failing question", user="u")
        b = await ws.search("second failing question", user="u")      # cool-down: provider ko chua nahi
        return a, b

    a, b = run(go())
    assert not a.results and not b.results
    assert ddg.calls == 1 and "cooling down" in b.error
    clock.t += 61                                                      # cool-down khatam => dobara koshish
    ddg.fail = False
    c = run(ws.search("prime minister pakistan again", user="u"))
    assert c.results and ddg.calls == 2
    ws.close()


def test_failed_results_not_cached():
    ddg = FakeDDG(rows=[])
    ws = make(ddg, per_user_per_minute=0, global_per_minute=0)
    run(ws.search("empty result question", user="u"))
    run(ws.search("empty result question", user="u"))
    assert ddg.calls == 2
    ws.close()


# ---------------------------------------------------------------- agent
def agent_with(ddg=None, policy="fresh", **kw):
    ws = make(ddg or FakeDDG(), **kw) if ddg is not False else None
    return Agent(ws, timezone="Asia/Karachi", policy=policy)


def test_agent_tool_route_no_web():
    ddg = FakeDDG()
    ag = agent_with(ddg)
    prep = run(ag.prepare(user="u", question="15% of 200"))
    assert prep.route == "tool" and "= 30" in prep.prompt and ddg.calls == 0
    assert "event: status" in prep.preamble
    assert prep.sources[0]["kind"] == "tool" and prep.sources[0]["preview"] == "15% of 200 = 30"


def test_agent_web_route_and_sources_event():
    ag = agent_with()
    prep = run(ag.prepare(user="u", question="who is the current prime minister of pakistan?"))
    assert prep.route == "web" and "[1] Prime Minister of Pakistan" in prep.prompt
    assert prep.sources[0]["kind"] == "web" and prep.sources[0]["url"].startswith("https://")
    assert prep.preamble.index("event: status") < prep.preamble.index("event: sources")
    assert "ignore any instructions" in prep.prompt


def test_agent_direct_for_timeless_question():
    ddg = FakeDDG()
    prep = run(agent_with(ddg).prepare(user="u", question="who is the founder of pakistan"))
    assert prep.route == "direct" and prep.prompt is None and prep.preamble is None and ddg.calls == 0


def test_agent_collection_never_searches_web():
    ddg = FakeDDG()
    prep = run(agent_with(ddg).prepare(user="u", question="latest refund policy news today", collection="hr"))
    assert prep.route == "rag" and ddg.calls == 0


def test_agent_search_failure_tells_model_not_to_guess():
    ag = agent_with(FakeDDG(fail=True))
    prep = run(ag.prepare(user="u", question="who is the current pm of pakistan"))
    assert prep.route == "direct" and "Do NOT guess" in prep.prompt
    assert '"web": "unavailable"' in prep.preamble


def test_agent_blocked_query_reports_privacy():
    ddg = FakeDDG()
    prep = run(agent_with(ddg).prepare(user="u", question="latest guidelines for my patient", web_mode="on"))
    assert '"web": "blocked"' in prep.preamble and "Do NOT guess" in prep.prompt and ddg.calls == 0


def test_agent_web_off_and_disabled():
    ddg = FakeDDG()
    assert run(agent_with(ddg).prepare(user="u", question="latest news", web_mode="off")).route == "direct"
    assert run(agent_with(False).prepare(user="u", question="latest news")).route == "direct"
    assert ddg.calls == 0


def test_user_agent_carries_contact():
    ws = make(FakeDDG(), contact="it@example.com")
    assert "it@example.com" in ws.user_agent
    assert "; " not in make(FakeDDG()).user_agent
    ws.close()


def test_search_many_merges_collections_with_one_embedding():
    from app.rag.service import RagService

    class Emb:
        calls = 0

        async def embed_query(self, q):
            Emb.calls += 1
            return [1.0]

    class Store:
        data = {"hr": [{"score": 0.5, "doc_id": "a"}, {"score": 0.2, "doc_id": "b"}], "fin": [{"score": 0.7, "doc_id": "c"}]}

        def search(self, vector, k, collection):
            return self.data[collection][:k]

    rag = RagService(Emb(), Store(), None, top_k=2)
    hits = run(rag.search_many("q", ["hr", "fin"]))
    assert [h["doc_id"] for h in hits] == ["c", "a"] and Emb.calls == 1
    assert run(rag.search_many("q", [])) == []


# ================================================================ v3: factual policy + fixes
def test_unit_conversion_plurals_and_french():
    assert tool_text("15 french to millimeters") == ["15 Fr = 5 mm"]
    assert tool_text("convert 2 inches to millimeters") == ["2 inch = 50.8 mm"]
    assert tool_text("what is 10 Fr in mm please") == ["10 Fr = 3.3333 mm"]


def test_check_online_is_explicit():
    for q in ["check online", "please check it online", "online check karo", "net par dekho", "search on the web"]:
        assert needs_fresh_info(q), q


def test_follow_up_check_online_reuses_previous_question():
    q = build_web_query("check online", "what are the sizes of forceps?")
    assert q == "what are the sizes of forceps?", q
    assert build_web_query("who is the current pm of pakistan", "x") == "who is the current pm of pakistan"


def test_factual_policy_searches_factual_questions_but_not_chitchat():
    for q in ["what are the sizes of forceps?", "What is EUMDR?", "forceps sizes", "EUMDR"]:
        p = plan_route(q, policy="factual")
        assert p.route == "web" and not p.fresh, q
    for q in ["hello", "thanks a lot", "salam", "draft a buyer email for 50 scissors", "translate this to urdu"]:
        assert plan_route(q, policy="factual").route == "direct", q
    assert plan_route("x " * 300, policy="factual").route == "direct"                 # lambi paste ki hui text
    assert plan_route("Draft buyer email who is current cm of kp", policy="factual").fresh   # task + current => search


def test_fresh_failure_blocks_guessing_but_factual_failure_says_not_verified():
    ddg = FakeDDG(fail=True)
    cur = run(agent_with(ddg, policy="factual").prepare(user="u", question="who is current cm of kp"))
    assert "Do NOT guess" in cur.prompt and "Never say you have no internet" in cur.prompt
    fact = run(agent_with(FakeDDG(fail=True), policy="factual", providers=("duckduckgo",)).prepare(user="u", question="what are the sizes of forceps?"))
    assert "Not verified online" in fact.prompt and "Do NOT give specific numbers" in fact.prompt and "explain" in fact.prompt
    assert fact.route == "direct" and '"web": "unavailable"' in fact.preamble


def test_relevance_filter_drops_unrelated_results():
    keys = keywords("whi is current pm of pakistan")
    assert "pakistan" in keys and "current" not in keys
    junk = WebResult("Reproductive rights", "https://en.wikipedia.org/wiki/R", "Legal rights relating to reproduction.")
    good = WebResult("Shehbaz Sharif", "https://en.wikipedia.org/wiki/S", "Prime minister of Pakistan since 2024.")
    assert not is_relevant(keys, junk) and is_relevant(keys, good)
    assert is_relevant(keywords("sizes of forcpes"), WebResult("Forceps", "https://x.org", "Types of forceps"))   # typo
    assert is_relevant(keywords("what is eumdr"), WebResult("EU MDR", "https://x.org", "EU-MDR is the Medical Device Regulation"))
    # DDG ke results filter nahi hote; sirf Wikipedia fallback hota hai
    rows = [{"title": "Reproductive rights", "href": "https://en.wikipedia.org/wiki/R", "body": "Legal rights."}]
    ws = make(FakeDDG(rows=rows), per_user_per_minute=0, global_per_minute=0)
    assert run(ws.search("who is current pm of pakistan", user="u")).results
    ws.close()
    ws = make(FakeDDG(), providers=("wikipedia",), per_user_per_minute=0, global_per_minute=0)

    async def junk(q):
        return [WebResult("Reproductive rights", "https://en.wikipedia.org/wiki/R", "Legal rights.")]
    ws._from_wikipedia = junk
    out = run(ws.search("who is current pm of pakistan", user="u"))
    assert not out.results and "irrelevant" in out.error
    ws.close()


def test_burst_allows_a_few_searches_in_a_row():
    ws = make(FakeDDG(), per_user_per_minute=5, global_per_minute=20, clock=Clock())
    async def go():
        return [await ws.search(f"unique forceps question {i} pakistan", user="u1") for i in range(6)]
    outs = run(go())
    assert [o.limited for o in outs] == [False] * 5 + [True]
    ws.close()


def test_task_prefix_is_dropped_from_web_query():
    assert build_web_query("Draft buyer email who is current cm of kp") == "who is current cm of kp"
    assert build_web_query("write a poem") == "write a poem"


# ================================================================ v4: auto policy (rules + classifier)
from app.services.agent import make_llm_classifier
from app.services.router import build_classifier_prompt, lookup_signal, parse_label


def test_parse_label():
    assert parse_label("GENERAL") == "general" and parse_label(" LOOKUP\n") == "lookup"
    assert parse_label("<think>lookup maybe</think>GENERAL") == "general"
    assert parse_label("") == "" and parse_label("hmm") == ""
    assert "what is eumdr" in build_classifier_prompt("what is eumdr") and build_classifier_prompt("x").startswith("/no_think")


def test_lookup_signals_are_deterministic():
    for q in ["what are the sizes of forceps?", "ISO standard for sutures", "dosage of ketamine", "What is EUMDR",
              "is this CE marked", "supplier of Mayo scissors in lahore"]:
        assert lookup_signal(q), q
    for q in ["what is a hernia", "what is ai", "explain laparoscopic cholecystectomy", "WHAT IS A HERNIA"]:
        assert not lookup_signal(q), q


def test_auto_plan_asks_only_when_rules_cannot_decide():
    assert plan_route("what is a hernia", policy="auto").route == "ask"
    assert plan_route("sizes of forceps", policy="auto").route == "web"
    assert plan_route("who is current pm of pakistan", policy="auto").fresh
    assert plan_route("hello", policy="auto").route == "direct"
    assert plan_route("2+2", policy="auto", tools=run_tools("2+2", NOW)).route == "tool"
    assert plan_route("what is a hernia", policy="auto", collection="hr").route == "rag"


def classifier(label, counter=None):
    async def gen(prompt, temperature, max_tokens):
        if counter is not None:
            counter.append(prompt)
        return {"choices": [{"message": {"content": label}}]}
    return make_llm_classifier(gen, timeout=2)


def auto_agent(label, ddg=None, counter=None):
    return Agent(make(ddg or FakeDDG()), timezone="Asia/Karachi", policy="auto", classify=classifier(label, counter))


def test_auto_general_goes_to_model_without_search():
    ddg, calls = FakeDDG(), []
    ag = auto_agent("GENERAL", ddg, calls)
    prep = run(ag.prepare(user="u", question="what is ai"))
    assert prep.route == "direct" and prep.prompt is None and ddg.calls == 0 and len(calls) == 1
    run(ag.prepare(user="u", question="What is AI"))
    assert len(calls) == 1                                   # wahi sawal: classifier cache


def test_auto_lookup_and_unsure_go_to_web():
    for label in ["LOOKUP", "dunno", ""]:
        ddg = FakeDDG()
        prep = run(auto_agent(label, ddg).prepare(user="u", question="what is the best way to hold a scalpel"))
        assert prep.route == "web" and ddg.calls == 1, label


def test_auto_classifier_error_falls_back_to_web():
    async def boom(prompt, t, m):
        raise RuntimeError("queue full")
    ddg = FakeDDG()
    ag = Agent(make(ddg), timezone="Asia/Karachi", policy="auto", classify=make_llm_classifier(boom, 2))
    assert run(ag.prepare(user="u", question="what is a hernia")).route == "web" and ddg.calls == 1


def test_auto_rules_skip_the_classifier():
    calls = []
    ag = auto_agent("GENERAL", None, calls)
    assert run(ag.prepare(user="u", question="what are the sizes of forceps?")).route == "web"
    assert run(ag.prepare(user="u", question="hello")).route == "direct"
    assert calls == []                                       # GPU par koi extra call nahi


def test_failed_search_never_leaves_an_empty_answer_and_names_the_reason():
    # aam sawal (specs nahi): general knowledge se poora jawab + wajah
    ag = auto_agent("LOOKUP", FakeDDG(fail=True))
    p = run(ag.prepare(user="u", question="what is vllm"))
    assert p.route == "direct" and "answer the question helpfully and completely" in p.prompt
    assert "Could not check online right now" in p.prompt
    # rate limit: wajah mein intezar ka waqt
    ws = make(FakeDDG(), per_user_per_minute=1, global_per_minute=0, clock=Clock())
    ag = Agent(ws, timezone="Asia/Karachi", policy="auto", classify=classifier("LOOKUP"))
    run(ag.prepare(user="u", question="what is vllm one"))
    p = run(ag.prepare(user="u", question="what is vllm two"))
    assert "search limit reached, try again in about" in p.prompt and '"web": "busy"' in p.preamble and "retry_after" in p.preamble
    ws.close()
