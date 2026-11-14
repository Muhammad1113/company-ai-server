import json

from app.services.sse import SseTextCollector


def _chunk(content=None, role=None, finish=None):
    delta = {}
    if role: delta["role"] = role
    if content is not None: delta["content"] = content
    return "data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": finish}]}) + "\n\n"


def test_collects_deltas_and_ignores_noise():
    c = SseTextCollector()
    for chunk in (_chunk(role="assistant", content=None), _chunk("Hel"), _chunk("lo "),
                  _chunk("wörld"), _chunk(finish="stop"), "data: [DONE]\n\n",
                  "data: {not json}\n\n", ": keep-alive\n\n", "event: error\n\n", "data: \n\n"):
        c.feed(chunk)
    assert c.text == "Hello wörld"


def test_handles_multiple_lines_in_one_chunk_and_empty_choices():
    c = SseTextCollector()
    c.feed(_chunk("A") + _chunk("B"))
    c.feed('data: {"choices": []}\n\n')
    c.feed('data: {"choices": null}\n\n')
    c.feed('data: {"choices":[{"delta":{"content":null}}]}\n\n')
    assert c.text == "AB"
