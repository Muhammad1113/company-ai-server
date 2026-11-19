"""
API-level tests (FastAPI + fake provider). Sync + asyncio.run, taake
pytest-asyncio install karne ki zaroorat na ho.
"""
import asyncio

import httpx
import pytest

from app.core.cache import MemoryTTLCache, ResponseCache
from app.core.concurrency import inference_controller
from app.core.ratelimit import TokenBucketLimiter
from app.core.security import parse_api_keys
from app.main import app
from app.services import container
from app.services.chat_service import chat_service

BODY = {"message": "hello", "temperature": 0.2, "max_tokens": 10}
STREAM = "/api/v1/chat/stream"
COMPLETIONS = "/api/v1/chat/completions"
USER_KEY = "bob-secret-key-00002"
ADMIN_KEY = "alice-secret-key-0001"


class FakeProvider:

    def __init__(self, chunks=None, fail=None):
        self.chunks = chunks or []
        self.fail = fail
        self.chat_calls = 0

    async def chat(self, payload):
        self.chat_calls += 1
        return {
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        }

    async def stream_chat(self, payload):
        if self.fail:
            raise self.fail
        for chunk in self.chunks:
            yield chunk


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(container, "api_keys", [])
    monkeypatch.setattr(container, "rate_limiter", None)
    monkeypatch.setattr(
        container, "response_cache",
        ResponseCache(MemoryTTLCache(), ttl=60),
    )


def _request(method, path, json=None, headers=None):
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.request(method, path, json=json, headers=headers)
    return asyncio.run(go())


def test_stream_success_releases_slot(monkeypatch):
    monkeypatch.setattr(
        chat_service, "provider",
        FakeProvider(chunks=["data: {}\n\n", "data: [DONE]\n\n"]),
    )
    r = _request("POST", STREAM, BODY)

    assert r.status_code == 200
    assert "[DONE]" in r.text
    assert "x-request-id" in r.headers
    assert inference_controller.active == 0


def test_provider_down_is_502_not_200(monkeypatch):
    monkeypatch.setattr(
        chat_service, "provider", FakeProvider(fail=httpx.ConnectError("down"))
    )
    r = _request("POST", STREAM, BODY)

    assert r.status_code == 502
    assert inference_controller.active == 0


def test_queue_full_is_429_with_retry_after(monkeypatch):
    monkeypatch.setattr(inference_controller, "max_queue_size", 0)

    async def go():
        ctx = inference_controller.slot()
        await ctx.__aenter__()                    # GPU slot busy
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                return await client.post(STREAM, json=BODY)
        finally:
            await ctx.__aexit__(None, None, None)

    r = asyncio.run(go())

    assert r.status_code == 429
    assert "retry-after" in r.headers
    assert inference_controller.active == 0


def test_metrics_no_redirect():
    r = _request("GET", "/metrics")
    assert r.status_code == 200
    assert "company_ai_active_inferences" in r.text


def test_completions_cache_hit(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(chat_service, "provider", provider)

    first = _request("POST", COMPLETIONS, BODY)
    second = _request("POST", COMPLETIONS, BODY)

    assert first.status_code == second.status_code == 200
    assert first.headers["x-cache"] == "MISS"
    assert second.headers["x-cache"] == "HIT"
    assert first.json()["response"] == second.json()["response"] == "hello"
    assert provider.chat_calls == 1


def test_high_temperature_bypasses_cache(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(chat_service, "provider", provider)

    hot = {**BODY, "temperature": 1.2}
    assert _request("POST", COMPLETIONS, hot).headers["x-cache"] == "BYPASS"
    assert _request("POST", COMPLETIONS, hot).headers["x-cache"] == "BYPASS"
    assert provider.chat_calls == 2


def test_auth_required(monkeypatch):
    monkeypatch.setattr(
        container, "api_keys", parse_api_keys(f"alice:{ADMIN_KEY}:admin")
    )
    monkeypatch.setattr(chat_service, "provider", FakeProvider())

    assert _request("POST", COMPLETIONS, BODY).status_code == 401

    bad = {"Authorization": "Bearer wrong-wrong-wrong-1"}
    assert _request("POST", COMPLETIONS, BODY, bad).status_code == 401

    good = {"Authorization": f"Bearer {ADMIN_KEY}"}
    assert _request("POST", COMPLETIONS, BODY, good).status_code == 200

    assert _request("GET", "/health").status_code == 200      # open endpoint


def test_rag_ingest_needs_admin(monkeypatch):
    monkeypatch.setattr(
        container, "api_keys",
        parse_api_keys(f"alice:{ADMIN_KEY}:admin,bob:{USER_KEY}:user"),
    )
    body = {"doc_id": "d1", "text": "hello world"}

    user = {"Authorization": f"Bearer {USER_KEY}"}
    assert _request("POST", "/api/v1/rag/ingest", body, user).status_code == 403


def test_rate_limit_counts_each_request_once(monkeypatch):
    monkeypatch.setattr(container, "rate_limiter", TokenBucketLimiter(60, 1))
    monkeypatch.setattr(chat_service, "provider", FakeProvider())

    first = _request("POST", COMPLETIONS, BODY)
    second = _request("POST", COMPLETIONS, BODY)

    assert first.status_code == 200            # double-count hota to 429 aata
    assert second.status_code == 429
    assert "retry-after" in second.headers
