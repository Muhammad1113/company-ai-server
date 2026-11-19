from app.services.messages import build_messages


def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def test_single_message_with_and_without_system_prompt():
    assert build_messages("hi", None) == [{"role": "user", "content": "hi"}]
    out = build_messages("hi", None, system_prompt="Be brief.")
    assert out[0] == {"role": "system", "content": "Be brief."} and out[1]["content"] == "hi"


def test_history_kept_in_order():
    hist = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"}]
    assert [m["content"] for m in build_messages(None, hist)] == ["q1", "a1", "q2"]


def test_oldest_turns_dropped_when_over_budget():
    hist = [{"role": "user", "content": "A" * 100}, {"role": "assistant", "content": "B" * 100},
            {"role": "user", "content": "C" * 100}, {"role": "assistant", "content": "D" * 100},
            {"role": "user", "content": "last"}]
    out = build_messages(None, hist, max_history_chars=220)
    assert out[-1]["content"] == "last"                      # aakhri hamesha
    assert [m["content"][0] for m in out] == ["C", "D", "l"]  # naye turns bache
    assert out[0]["role"] == "user"


def test_leading_assistant_turn_is_dropped():
    hist = [{"role": "user", "content": "A" * 100}, {"role": "assistant", "content": "B" * 10},
            {"role": "user", "content": "last"}]
    out = build_messages(None, hist, max_history_chars=30)
    assert [m["role"] for m in out] == ["user"] and out[0]["content"] == "last"


def test_last_message_kept_even_if_over_budget():
    out = build_messages("X" * 500, None, max_history_chars=10)
    assert len(out) == 1 and len(out[0]["content"]) == 500


def test_validation():
    assert _raises(lambda: build_messages(None, None))
    assert _raises(lambda: build_messages("a", [{"role": "user", "content": "b"}]))
    assert _raises(lambda: build_messages(None, [{"role": "assistant", "content": "b"}]))
    assert _raises(lambda: build_messages(None, []))
