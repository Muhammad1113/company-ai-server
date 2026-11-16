"""vLLM par switch: llama.cpp-specific `grammar` field sirf llama_cpp provider ko jata hai."""
from app.core.config import settings
from app.services.chat_service import ChatService


def _payload(monkeypatch, provider: str) -> dict:
    monkeypatch.setattr(settings, "INFERENCE_PROVIDER", provider)
    monkeypatch.setattr(settings, "LATIN_ONLY_OUTPUT", True)
    return ChatService().build_payload("hello", 0.2, 50)


def test_llama_cpp_gets_latin_grammar(monkeypatch):
    assert "grammar" in _payload(monkeypatch, "llama_cpp")


def test_vllm_and_openai_compatible_do_not_get_grammar(monkeypatch):
    for name in ("vllm", "openai_compatible"):
        p = _payload(monkeypatch, name)
        assert "grammar" not in p
        assert p["model"] == settings.LLAMA_MODEL and p["messages"][0]["content"] == "hello"
