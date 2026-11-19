"""v6: pichla sawal sirf asli follow-up par jurta hai; Auto mode mein documents kab chhodne hain."""
from app.services.router import (
    build_web_query, contextual_text, docs_min_score, is_follow_up, plan_route,
)
from app.services.websearch import privacy_block_reason

KP = "who is current cm of kp"
FORCEPS = "what are the sizes of forceps?"


def test_complete_questions_are_never_follow_ups():
    for q, prev in [("what is computer", KP), ("vllm kia hai?", FORCEPS), ("what is llama.cpp", "vllm kia hai?"),
                    ("what is vllm", "x y z"), ("scalpel kya hota hai", FORCEPS)]:
        assert not is_follow_up(q, prev), q
        assert build_web_query(q, prev) in {"what is computer", "vllm", "what is llama.cpp", "what is vllm", "scalpel"}


def test_real_follow_ups_still_join_previous():
    assert is_follow_up("check online", FORCEPS)
    assert build_web_query("check online", FORCEPS).startswith("what are the sizes of forceps")
    assert is_follow_up("aur Punjab ka?", KP)
    assert "cm of kp" in build_web_query("aur Punjab ka?", KP)
    assert is_follow_up("what about its price", FORCEPS)


def test_no_previous_or_numeric_previous_means_no_follow_up():
    assert not is_follow_up("check online", "")
    assert not is_follow_up("check online", "453+5465-5645+45")
    assert build_web_query("what is llama.cpp", "453+5465-5645+45") == "what is llama.cpp"
    assert privacy_block_reason(build_web_query("what is llama.cpp", "453+5465-5645+45")) == ""


def test_roman_urdu_filler_removed_from_web_query():
    assert build_web_query("vllm kia hai?", "") == "vllm"
    assert build_web_query("scalpel kya hota hai", "") == "scalpel"
    assert build_web_query("what is ISO 13485", "") == "what is ISO 13485"


def test_retrieval_text_only_joins_follow_ups():
    assert contextual_text("what is computer", KP) == "what is computer"
    assert contextual_text("check online", FORCEPS).startswith(FORCEPS)


def test_docs_threshold():
    assert docs_min_score("check online", 0.4) is None
    assert docs_min_score("search the web for vllm", 0.4) is None
    assert docs_min_score("who is current cm of KP", 0.4) == 0.4 + 0.15
    assert docs_min_score("latest treatment for my patient", 0.4) > 0.4
    assert docs_min_score("Surg05 ka RAG status kya hai", 0.4) == 0.4
    assert docs_min_score("what is a hernia", 0.4) == 0.4


def test_check_online_routes_to_web_with_previous_question():
    p = plan_route("check online", FORCEPS)
    assert p.route == "web" and "sizes of forceps" in p.web_query


def test_plain_question_after_calculator_not_blocked():
    p = plan_route("what is llama.cpp", "453+5465-5645+45")
    assert privacy_block_reason(p.web_query) == ""
