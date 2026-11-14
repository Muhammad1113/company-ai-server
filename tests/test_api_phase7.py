"""API-level: RAG inside conversations, per-client cap, change password, collections list."""
import asyncio
import json

import httpx
import pytest

from app.core.cache import MemoryTTLCache, ResponseCache
from app.core.config import settings
from app.core.fairness import ClientConcurrencyLimiter
from app.core.ratelimit import TokenBucketLimiter
from app.core.security import parse_api_keys
from app.database.accounts import AccountsRepository
from app.database.conversations import ConversationRepository
from app.main import app
from app.rag.embeddings import HashingEmbedder
from app.rag.service import RagService
from app.rag.store import JsonVectorStore
from app.services import container
from app.services.chat_service import chat_service

ROOT_KEY = "root-admin-secret-key-001"
PASSWORD = "correct-horse-1"
BASE = "/api/v1"
COMPANY = "testco"
MINE = "co_testco__docs"
OTHER = "co_otherco__docs"
REFUND = "Refund policy: customers can request a refund within 30 days of purchase."
LEAVE = "Leave policy: employees receive 20 days of annual leave every year."


class FakeProvider:
    def __init__(self, fail=None):
        self.fail = fail
        self.last_payload = None

    async def chat(self, payload):
        self.last_payload = payload
        return {"choices": [{"message": {"content": "hello"}}], "usage": None}

    async def stream_chat(self, payload):
        self.last_payload = payload
        if self.fail:
            raise self.fail
        for part in ("He", "llo"):
            yield "data: " + json.dumps({"choices": [{"delta": {"content": part}}]}) + "\n\n"
        yield "data: [DONE]\n\n"


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    accounts = AccountsRepository(":memory:", scrypt_n=2 ** 10)
    conversations = ConversationRepository(":memory:")
    asyncio.run(accounts.init())
    asyncio.run(accounts.create_company(COMPANY, "Test Co"))
    asyncio.run(conversations.init())

    emb = HashingEmbedder(256)

    async def gen(message, temperature, max_tokens):
        return {"choices": [{"message": {"content": "ok"}}], "usage": None}

    rag = RagService(emb, JsonVectorStore(None, emb.id), gen, top_k=3)

    provider = FakeProvider()
    monkeypatch.setattr(settings, "CHAT_SYSTEM_PROMPT", "")
    from app.api.routes import chat as _chat_routes, conversations as _conv_routes
    monkeypatch.setattr(_chat_routes, "SYSTEM_PROMPT", "")            # routes module-level constant use karte hain
    monkeypatch.setattr(_conv_routes, "SYSTEM_PROMPT", "")
    monkeypatch.setattr(container, "agent", None)                      # in tests mein internet/clock agent nahi (network par depend nahi)
    monkeypatch.setattr(container, "accounts_repo", accounts)
    monkeypatch.setattr(container, "conversations_repo", conversations)
    monkeypatch.setattr(container, "rag_service", rag)
    monkeypatch.setattr(container, "login_limiter", TokenBucketLimiter(600, 100))
    monkeypatch.setattr(container, "client_limiter", ClientConcurrencyLimiter(2))
    monkeypatch.setattr(container, "api_keys", [])
    monkeypatch.setattr(container, "rate_limiter", None)
    monkeypatch.setattr(container, "response_cache", ResponseCache(MemoryTTLCache(), ttl=60))
    monkeypatch.setattr(chat_service, "provider", provider)

    class Env:
        pass
    env = Env()
    env.accounts, env.rag, env.provider = accounts, rag, provider
    return env


def _request(method, path, json_body=None, headers=None):
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, json=json_body, headers=headers)
    return asyncio.run(go())


def _user(env, username, role="user"):
    company = None if role == "admin" else COMPANY
    asyncio.run(env.accounts.create_user(username, PASSWORD, role, None, company))


def _bearer(username, password=PASSWORD):
    r = _request("POST", f"{BASE}/auth/login", {"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _conversation(headers):
    return _request("POST", f"{BASE}/conversations", {}, headers).json()["id"]


def _seed(env):
    asyncio.run(env.rag.ingest("refund", REFUND, MINE))        # apni company
    asyncio.run(env.rag.ingest("leave", LEAVE, OTHER))         # doosri company: kabhi nazar nahi aani chahiye


# ---------------- change password ----------------
def test_change_password_flow(isolate):
    _user(isolate, "alice")
    old = _bearer("alice")
    url = f"{BASE}/auth/change-password"

    bad = _request("POST", url, {"current_password": "WRONG-password", "new_password": "brand-new-pass-9"}, old)
    assert bad.status_code == 400 and "incorrect" in bad.json()["detail"]
    assert _request("POST", url, {"current_password": PASSWORD, "new_password": "short"}, old).status_code == 422

    ok = _request("POST", url, {"current_password": PASSWORD, "new_password": "brand-new-pass-9"}, old)
    assert ok.status_code == 200
    new = {"Authorization": f"Bearer {ok.json()['token']}"}

    assert _request("GET", f"{BASE}/auth/me", headers=old).status_code == 401       # purana session band
    assert _request("GET", f"{BASE}/auth/me", headers=new).json()["username"] == "alice"
    assert _request("POST", f"{BASE}/auth/login", {"username": "alice", "password": PASSWORD}).status_code == 401
    assert _request("POST", f"{BASE}/auth/login", {"username": "alice", "password": "brand-new-pass-9"}).status_code == 200


def test_change_password_not_for_api_key_clients(monkeypatch):
    monkeypatch.setattr(container, "api_keys", parse_api_keys(f"root:{ROOT_KEY}:admin"))
    r = _request("POST", f"{BASE}/auth/change-password",
                 {"current_password": "whatever-1", "new_password": "another-pass-22"},
                 {"Authorization": f"Bearer {ROOT_KEY}"})
    assert r.status_code == 400 and "user accounts" in r.json()["detail"]


# ---------------- RAG inside conversations ----------------
def test_chat_with_collection_streams_sources_and_persists(isolate):
    _user(isolate, "hr_user")
    _seed(isolate)
    h = _bearer("hr_user")
    cid = _conversation(h)

    question = "Can I get a refund within 30 days?"
    r = _request("POST", f"{BASE}/conversations/{cid}/chat", {"message": question, "collection": MINE}, h)

    assert r.status_code == 200
    assert "event: sources" in r.text and '"doc_id": "refund"' in r.text
    assert r.text.index("event: sources") < r.text.index('"choices"')          # sources pehle aate hain
    assert "[DONE]" in r.text

    sent = isolate.provider.last_payload["messages"][-1]["content"]
    assert "Context:" in sent and "refund within 30 days" in sent and question in sent

    msgs = _request("GET", f"{BASE}/conversations/{cid}", headers=h).json()["messages"]
    assert msgs[0]["content"] == question                                      # DB mein asli sawal, prompt nahi
    assert msgs[1]["role"] == "assistant" and msgs[1]["sources"][0]["doc_id"] == "refund"


def test_collection_access_enforced_and_nothing_saved(isolate):
    _user(isolate, "hr_user")
    _seed(isolate)
    h = _bearer("hr_user")
    cid = _conversation(h)

    r = _request("POST", f"{BASE}/conversations/{cid}/chat", {"message": "leave days?", "collection": OTHER}, h)
    assert r.status_code == 403
    assert _request("GET", f"{BASE}/conversations/{cid}", headers=h).json()["messages"] == []


def test_no_relevant_documents_is_explicit(isolate):
    _user(isolate, "hr_user")
    _seed(isolate)
    h = _bearer("hr_user")
    cid = _conversation(h)

    r = _request("POST", f"{BASE}/conversations/{cid}/chat",
                 {"message": "zebra giraffe xylophone", "collection": MINE}, h)
    assert r.status_code == 200 and '"sources": []' in r.text
    assert "(no relevant documents found)" in isolate.provider.last_payload["messages"][-1]["content"]
    assert _request("GET", f"{BASE}/conversations/{cid}", headers=h).json()["messages"][1]["sources"] is None


def test_follow_up_question_retrieves_using_previous_question(isolate):
    _user(isolate, "hr_user")
    _seed(isolate)
    h = _bearer("hr_user")
    cid = _conversation(h)
    url = f"{BASE}/conversations/{cid}/chat"

    _request("POST", url, {"message": "refund policy", "collection": MINE}, h)
    r = _request("POST", url, {"message": "what about digital goods", "collection": MINE}, h)

    assert r.status_code == 200
    assert "refund within 30 days" in isolate.provider.last_payload["messages"][-1]["content"]


def test_regenerate_keeps_rag(isolate):
    _user(isolate, "hr_user")
    _seed(isolate)
    h = _bearer("hr_user")
    cid = _conversation(h)
    url = f"{BASE}/conversations/{cid}/chat"

    _request("POST", url, {"message": "refund within 30 days?", "collection": MINE}, h)
    r = _request("POST", url, {"regenerate": True, "collection": MINE}, h)
    assert r.status_code == 200 and "event: sources" in r.text

    msgs = _request("GET", f"{BASE}/conversations/{cid}", headers=h).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["sources"]


def test_collections_list_respects_access(isolate, monkeypatch):
    _user(isolate, "hr_user")
    _seed(isolate)

    hr = _request("GET", f"{BASE}/rag/collections", headers=_bearer("hr_user")).json()["collections"]
    assert hr == [{"name": MINE, "documents": 1, "scope": "company", "can_manage": False}]   # OTHER bilkul nahi

    monkeypatch.setattr(container, "api_keys", parse_api_keys(f"root:{ROOT_KEY}:admin"))
    admin = _request("GET", f"{BASE}/rag/collections", headers={"Authorization": f"Bearer {ROOT_KEY}"}).json()
    assert [c["name"] for c in admin["collections"]] == [OTHER, MINE]


# ---------------- per-client concurrency ----------------
def test_busy_client_gets_429_but_others_do_not(isolate, monkeypatch):
    limiter = ClientConcurrencyLimiter(1)
    monkeypatch.setattr(container, "client_limiter", limiter)
    _user(isolate, "alice"); _user(isolate, "bob")
    alice, bob = _bearer("alice"), _bearer("bob")
    cid = _conversation(alice)

    limiter.acquire("alice")                                                   # alice ki ek request "chal rahi" hai
    c = _request("POST", f"{BASE}/chat/completions", {"message": "hi"}, alice)
    s = _request("POST", f"{BASE}/conversations/{cid}/chat", {"message": "hi"}, alice)
    assert c.status_code == 429 and s.status_code == 429 and "retry-after" in s.headers
    assert _request("POST", f"{BASE}/chat/completions", {"message": "hi"}, bob).status_code == 200

    limiter.release("alice")
    assert _request("POST", f"{BASE}/chat/completions", {"message": "hi"}, alice).status_code == 200


def test_client_slots_are_released_on_success_and_failure(isolate, monkeypatch):
    limiter = ClientConcurrencyLimiter(1)
    monkeypatch.setattr(container, "client_limiter", limiter)
    _user(isolate, "alice")
    h = _bearer("alice")

    assert _request("POST", f"{BASE}/chat/stream", {"message": "hi"}, h).status_code == 200
    assert limiter._active == {}

    monkeypatch.setattr(chat_service, "provider", FakeProvider(fail=httpx.ConnectError("down")))
    assert _request("POST", f"{BASE}/chat/stream", {"message": "hi"}, h).status_code == 502
    assert _request("POST", f"{BASE}/chat/completions", {"message": "other"}, h).status_code in (200, 502)
    assert limiter._active == {}                                               # koi leak nahi
