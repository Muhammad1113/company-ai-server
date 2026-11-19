"""API-level: file upload, collection ACL, multi-turn messages (offline: fake provider + hashing RAG)."""
import asyncio

import httpx
import pytest

from app.core.cache import MemoryTTLCache, ResponseCache
from app.core.config import settings
from app.core.security import parse_api_keys
from app.main import app
from app.rag.embeddings import HashingEmbedder
from app.rag.service import RagService
from app.rag.store import JsonVectorStore
from app.services import container
from app.services.chat_service import chat_service
from tests.test_extractors import make_docx, make_pdf

ROOT_KEY = "root-admin-secret-key-001"
HR_KEY = "hr-user-secret-key-00002"
INGEST = "/api/v1/rag/ingest-file"
TEXT = b"Refund policy: customers can request a refund within 30 days of purchase."


class FakeProvider:
    def __init__(self):
        self.last_payload = None

    async def chat(self, payload):
        self.last_payload = payload
        return {"choices": [{"message": {"content": "hello"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1}}

    async def stream_chat(self, payload):
        self.last_payload = payload
        yield "data: {}\n\n"
        yield "data: [DONE]\n\n"


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    emb = HashingEmbedder(256)

    async def gen(message, temperature, max_tokens):
        return {"choices": [{"message": {"content": "ok"}}], "usage": None}

    monkeypatch.setattr(settings, "CHAT_SYSTEM_PROMPT", "")        # .env ki value se test na tootein
    from app.api.routes import chat as _chat_routes, conversations as _conv_routes
    monkeypatch.setattr(_chat_routes, "SYSTEM_PROMPT", "")            # routes module-level constant use karte hain
    monkeypatch.setattr(_conv_routes, "SYSTEM_PROMPT", "")
    monkeypatch.setattr(container, "api_keys", [])
    monkeypatch.setattr(container, "rate_limiter", None)
    monkeypatch.setattr(container, "response_cache", ResponseCache(MemoryTTLCache(), ttl=60))
    monkeypatch.setattr(container, "rag_service",
                        RagService(emb, JsonVectorStore(None, emb.id), gen, top_k=3))


def _request(method, path, json=None, headers=None, content=None):
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, json=json, content=content, headers=headers)
    return asyncio.run(go())


def _keys(monkeypatch):
    monkeypatch.setattr(container, "api_keys", parse_api_keys(
        f"root:{ROOT_KEY}:admin,hr:{HR_KEY}:user:hr"))
    return ({"Authorization": f"Bearer {ROOT_KEY}"}, {"Authorization": f"Bearer {HR_KEY}"})


# ---------------- file upload ----------------
def test_ingest_txt_then_search_finds_it():
    r = _request("POST", f"{INGEST}?filename=refund.txt&collection=hr", content=TEXT)
    assert r.status_code == 200, r.text
    assert r.json()["doc_id"] == "refund" and r.json()["type"] == "txt" and r.json()["chunks"] >= 1

    s = _request("POST", "/api/v1/rag/search", json={"question": "refund within 30 days", "collection": "hr"})
    assert s.status_code == 200 and s.json()["hits"][0]["doc_id"] == "refund"


def test_ingest_pdf_and_docx():
    pdf = make_pdf(["Refunds are paid to the original payment method within five days. " * 2])
    r = _request("POST", f"{INGEST}?filename=refunds.pdf&collection=hr", content=pdf)
    assert r.status_code == 200 and r.json()["pages"] == 1 and r.json()["type"] == "pdf"

    r = _request("POST", f"{INGEST}?filename=leave.docx&collection=hr&doc_id=leave-policy", content=make_docx())
    assert r.status_code == 200 and r.json()["doc_id"] == "leave-policy"


def test_upload_errors_have_clear_status_codes(monkeypatch):
    assert _request("POST", f"{INGEST}?filename=a.xlsx", content=b"x").status_code == 415
    assert _request("POST", f"{INGEST}?filename=a.txt", content=b"").status_code == 422

    scanned = _request("POST", f"{INGEST}?filename=scan.pdf", content=make_pdf(["", ""]))
    assert scanned.status_code == 422 and "OCR" in scanned.json()["detail"]

    monkeypatch.setattr(settings, "RAG_MAX_UPLOAD_MB", 0.0001)          # ~100 bytes
    assert _request("POST", f"{INGEST}?filename=a.txt", content=b"x" * 500).status_code == 413


def test_upload_is_admin_only(monkeypatch):
    admin, hr = _keys(monkeypatch)
    assert _request("POST", f"{INGEST}?filename=a.txt", content=TEXT, headers=hr).status_code == 403
    assert _request("POST", f"{INGEST}?filename=a.txt", content=TEXT).status_code == 401
    assert _request("POST", f"{INGEST}?filename=a.txt", content=TEXT, headers=admin).status_code == 200


# ---------------- collection ACL ----------------
def test_user_limited_to_allowed_collections(monkeypatch):
    admin, hr = _keys(monkeypatch)
    _request("POST", f"{INGEST}?filename=refund.txt&collection=finance", content=TEXT, headers=admin)
    _request("POST", f"{INGEST}?filename=refund.txt&collection=hr", content=TEXT, headers=admin)

    ok = _request("POST", "/api/v1/rag/search", json={"question": "refund", "collection": "hr"}, headers=hr)
    assert ok.status_code == 200 and ok.json()["hits"]

    for path in ("/api/v1/rag/search", "/api/v1/rag/query"):
        denied = _request("POST", path, json={"question": "refund", "collection": "finance"}, headers=hr)
        assert denied.status_code == 403, path

    allowed_admin = _request("POST", "/api/v1/rag/search", json={"question": "refund", "collection": "finance"}, headers=admin)
    assert allowed_admin.status_code == 200 and allowed_admin.json()["hits"]


# ---------------- multi-turn chat ----------------
def test_messages_history_and_system_prompt_reach_provider(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(chat_service, "provider", provider)
    from app.api.routes import chat as _chat_routes
    monkeypatch.setattr(_chat_routes, "SYSTEM_PROMPT", "You are the company assistant.")

    body = {"messages": [{"role": "user", "content": "What is the refund window?"},
                         {"role": "assistant", "content": "30 days."},
                         {"role": "user", "content": "And for digital goods?"}],
            "max_tokens": 20}
    r = _request("POST", "/api/v1/chat/completions", json=body)

    assert r.status_code == 200 and r.json()["response"] == "hello"
    sent = provider.last_payload["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[0]["content"] == "You are the company assistant."
    assert sent[-1]["content"] == "And for digital goods?"


def test_single_message_still_works_and_streams_with_messages(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(chat_service, "provider", provider)

    assert _request("POST", "/api/v1/chat/completions", json={"message": "hi"}).status_code == 200
    assert provider.last_payload["messages"] == [{"role": "user", "content": "hi"}]

    r = _request("POST", "/api/v1/chat/stream",
                 json={"messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
                                    {"role": "user", "content": "c"}]})
    assert r.status_code == 200 and "[DONE]" in r.text
    assert [m["content"] for m in provider.last_payload["messages"]] == ["a", "b", "c"]


def test_different_history_is_not_served_from_cache(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(chat_service, "provider", provider)
    turn = lambda q: {"messages": [{"role": "user", "content": "context"},
                                   {"role": "assistant", "content": "ok"},
                                   {"role": "user", "content": q}]}

    a = _request("POST", "/api/v1/chat/completions", json=turn("question one"))
    b = _request("POST", "/api/v1/chat/completions", json=turn("question two"))
    c = _request("POST", "/api/v1/chat/completions", json=turn("question one"))
    assert (a.headers["x-cache"], b.headers["x-cache"], c.headers["x-cache"]) == ("MISS", "MISS", "HIT")


def test_invalid_message_shapes_are_422():
    url = "/api/v1/chat/completions"
    both = {"message": "a", "messages": [{"role": "user", "content": "b"}]}
    assert _request("POST", url, json=both).status_code == 422
    assert _request("POST", url, json={}).status_code == 422
    assert _request("POST", url, json={"messages": [{"role": "assistant", "content": "x"}]}).status_code == 422
    assert _request("POST", url, json={"messages": [{"role": "system", "content": "x"}]}).status_code == 422
