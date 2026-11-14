"""API-level: login/sessions, admin users, conversations (ownership, streaming persist, regenerate)."""
import asyncio
import json

import httpx
import pytest

from app.core.cache import MemoryTTLCache, ResponseCache
from app.core.config import settings
from app.core.ratelimit import TokenBucketLimiter
from app.core.security import parse_api_keys
from app.database.accounts import AccountsRepository
from app.database.conversations import ConversationRepository
from app.main import app
from app.services import container
from app.services.chat_service import chat_service

ROOT_KEY = "root-admin-secret-key-001"
PASSWORD = "correct-horse-1"
BASE = "/api/v1"
COMPANY = "testco"
MINE = "co_testco__docs"
OTHER = "co_otherco__docs"


class FakeProvider:
    def __init__(self, replies=None, fail=None):
        self.replies = list(replies or ["Hello"])
        self.fail = fail
        self.calls = 0
        self.last_payload = None

    async def chat(self, payload):
        self.last_payload = payload
        return {"choices": [{"message": {"content": "hello"}}], "usage": None}

    async def stream_chat(self, payload):
        self.last_payload = payload
        if self.fail:
            raise self.fail
        text = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        for i in range(0, len(text), 2):
            yield "data: " + json.dumps({"choices": [{"delta": {"content": text[i:i + 2]}}]}) + "\n\n"
        yield "data: [DONE]\n\n"


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    accounts = AccountsRepository(":memory:", scrypt_n=2 ** 10)
    conversations = ConversationRepository(":memory:")
    asyncio.run(accounts.init())
    asyncio.run(conversations.init())
    asyncio.run(accounts.create_company(COMPANY, "Test Co"))         # har non-admin user kisi company ka hota hai

    monkeypatch.setattr(settings, "CHAT_SYSTEM_PROMPT", "")
    from app.api.routes import chat as _chat_routes, conversations as _conv_routes
    monkeypatch.setattr(_chat_routes, "SYSTEM_PROMPT", "")            # routes module-level constant use karte hain
    monkeypatch.setattr(_conv_routes, "SYSTEM_PROMPT", "")
    monkeypatch.setattr(container, "agent", None)                      # in tests mein internet/clock agent nahi (network par depend nahi)
    monkeypatch.setattr(container, "accounts_repo", accounts)
    monkeypatch.setattr(container, "conversations_repo", conversations)
    monkeypatch.setattr(container, "login_limiter", TokenBucketLimiter(600, 100))
    monkeypatch.setattr(container, "api_keys", [])
    monkeypatch.setattr(container, "rate_limiter", None)
    monkeypatch.setattr(container, "response_cache", ResponseCache(MemoryTTLCache(), ttl=60))
    monkeypatch.setattr(chat_service, "provider", FakeProvider())
    return accounts


def _request(method, path, json_body=None, headers=None):
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, json=json_body, headers=headers)
    return asyncio.run(go())


def _user(accounts, username, role="user"):
    company = None if role == "admin" else COMPANY
    asyncio.run(accounts.create_user(username, PASSWORD, role, None, company))


def _login(username, password=PASSWORD):
    r = _request("POST", f"{BASE}/auth/login", {"username": username, "password": password})
    return r


def _bearer(username):
    return {"Authorization": f"Bearer {_login(username).json()['token']}"}


def _new_conversation(headers):
    r = _request("POST", f"{BASE}/conversations", {}, headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------- login / sessions ----------------
def test_login_me_logout_flow(isolate):
    _user(isolate, "alice")

    assert _login("alice", "wrong-password").status_code == 401
    assert _login("ghost").status_code == 401

    ok = _login("alice")
    assert ok.status_code == 200 and ok.json()["user"] == {
        "username": "alice", "role": "user", "company": COMPANY,
        "default_collection": MINE, "collections": None}
    headers = {"Authorization": f"Bearer {ok.json()['token']}"}

    me = _request("GET", f"{BASE}/auth/me", headers=headers)
    assert me.status_code == 200 and me.json()["username"] == "alice"
    assert me.json()["company"] == COMPANY and me.json()["default_collection"] == MINE

    assert _request("POST", f"{BASE}/auth/logout", headers=headers).status_code == 200
    assert _request("GET", f"{BASE}/auth/me", headers=headers).status_code == 401


def test_auth_switches_on_when_first_user_exists(isolate):
    body = {"message": "hi"}
    assert _request("POST", f"{BASE}/chat/completions", body).status_code == 200      # dev mode

    _user(isolate, "alice")
    assert _request("POST", f"{BASE}/chat/completions", body).status_code == 401      # ab lock
    assert _request("POST", f"{BASE}/chat/completions", body, _bearer("alice")).status_code == 200


def test_session_collections_enforced_in_rag(isolate):
    _user(isolate, "hr_person")
    headers = _bearer("hr_person")
    denied = _request("POST", f"{BASE}/rag/search", {"question": "x", "collection": OTHER}, headers)
    assert denied.status_code == 403                                   # doosri company ki collection
    assert _request("POST", f"{BASE}/rag/search", {"question": "x", "collection": "default"}, headers).status_code == 403
    assert _request("POST", f"{BASE}/rag/search", {"question": "x", "collection": MINE}, headers).status_code == 200


def test_login_rate_limited(monkeypatch, isolate):
    monkeypatch.setattr(container, "login_limiter", TokenBucketLimiter(60, 2))
    _user(isolate, "alice")
    codes = [_login("alice", "bad-password-x").status_code for _ in range(4)]
    assert codes[:2] == [401, 401] and codes[2] == 429
    assert "retry-after" in _login("alice").headers


# ---------------- admin ----------------
def test_admin_manages_users(monkeypatch, isolate):
    monkeypatch.setattr(container, "api_keys", parse_api_keys(f"root:{ROOT_KEY}:admin"))
    admin = {"Authorization": f"Bearer {ROOT_KEY}"}

    weak = _request("POST", f"{BASE}/admin/users", {"username": "bob", "password": "short", "company": COMPANY}, admin)
    assert weak.status_code == 422

    # company ke baghair user nahi banta
    assert _request("POST", f"{BASE}/admin/users", {"username": "bob", "password": PASSWORD}, admin).status_code == 400
    assert _request("POST", f"{BASE}/admin/users",
                    {"username": "bob", "password": PASSWORD, "company": "nosuch"}, admin).status_code == 400

    created = _request("POST", f"{BASE}/admin/users",
                       {"username": "bob", "password": PASSWORD, "company": COMPANY}, admin)
    assert created.status_code == 201 and created.json()["company"] == COMPANY and created.json()["collections"] is None
    assert _request("POST", f"{BASE}/admin/users",
                    {"username": "BOB", "password": PASSWORD, "company": COMPANY}, admin).status_code == 400   # duplicate

    bob = _bearer("bob")
    assert _request("GET", f"{BASE}/admin/users", headers=bob).status_code == 403          # user admin nahi

    listed = _request("GET", f"{BASE}/admin/users", headers=admin).json()["users"]
    assert [u["username"] for u in listed] == ["bob"]

    # disable karo => session bhi band (company users par collections null bhejna harmless hai)
    patched = _request("PATCH", f"{BASE}/admin/users/bob", {"collections": None, "disabled": True}, admin)
    assert patched.status_code == 200
    assert _request("GET", f"{BASE}/auth/me", headers=bob).status_code == 401
    assert _login("bob").status_code == 401
    assert _request("PATCH", f"{BASE}/admin/users/ghost", {"disabled": True}, admin).status_code == 404


# ---------------- conversations ----------------
def test_conversation_crud_and_ownership(isolate):
    _user(isolate, "alice"); _user(isolate, "bob")
    alice, bob = _bearer("alice"), _bearer("bob")

    cid = _new_conversation(alice)
    assert _request("GET", f"{BASE}/conversations", headers=alice).json()["conversations"][0]["id"] == cid
    assert _request("GET", f"{BASE}/conversations", headers=bob).json()["conversations"] == []

    for method, body in (("GET", None), ("PATCH", {"title": "x"}), ("DELETE", None), ("POST", {"message": "hi"})):
        path = f"{BASE}/conversations/{cid}" + ("/chat" if method == "POST" else "")
        assert _request(method, path, body, bob).status_code == 404, method              # bob ko nahi dikhta

    assert _request("PATCH", f"{BASE}/conversations/{cid}", {"title": "Leave"}, alice).json()["title"] == "Leave"
    assert _request("DELETE", f"{BASE}/conversations/{cid}", headers=alice).status_code == 200
    assert _request("GET", f"{BASE}/conversations/{cid}", headers=alice).status_code == 404
    assert _request("GET", f"{BASE}/conversations/not-an-id", headers=alice).status_code == 422


def test_chat_streams_and_persists_history(isolate, monkeypatch):
    provider = FakeProvider(replies=["Hello there"])
    monkeypatch.setattr(chat_service, "provider", provider)
    _user(isolate, "alice")
    h = _bearer("alice")
    cid = _new_conversation(h)

    r = _request("POST", f"{BASE}/conversations/{cid}/chat", {"message": "What is the refund window?"}, h)
    assert r.status_code == 200 and "[DONE]" in r.text and "He" in r.text

    conv = _request("GET", f"{BASE}/conversations/{cid}", headers=h).json()
    assert conv["title"] == "What is the refund window?"
    assert [(m["role"], m["content"], m["status"]) for m in conv["messages"]] == [
        ("user", "What is the refund window?", "complete"),
        ("assistant", "Hello there", "complete")]

    _request("POST", f"{BASE}/conversations/{cid}/chat", {"message": "And digital goods?"}, h)
    sent = provider.last_payload["messages"]
    assert [m["content"] for m in sent] == ["What is the refund window?", "Hello there", "And digital goods?"]


def test_regenerate_replaces_last_answer(isolate, monkeypatch):
    monkeypatch.setattr(chat_service, "provider", FakeProvider(replies=["first answer", "second answer"]))
    _user(isolate, "alice")
    h = _bearer("alice")
    cid = _new_conversation(h)

    assert _request("POST", f"{BASE}/conversations/{cid}/chat", {"regenerate": True}, h).status_code == 400   # kuch nahi

    _request("POST", f"{BASE}/conversations/{cid}/chat", {"message": "question"}, h)
    r = _request("POST", f"{BASE}/conversations/{cid}/chat", {"regenerate": True}, h)
    assert r.status_code == 200

    msgs = _request("GET", f"{BASE}/conversations/{cid}", headers=h).json()["messages"]
    assert [(m["role"], m["content"]) for m in msgs] == [("user", "question"), ("assistant", "second answer")]


def test_failed_generation_saves_nothing(isolate, monkeypatch):
    monkeypatch.setattr(chat_service, "provider", FakeProvider(fail=httpx.ConnectError("down")))
    _user(isolate, "alice")
    h = _bearer("alice")
    cid = _new_conversation(h)

    r = _request("POST", f"{BASE}/conversations/{cid}/chat", {"message": "hello?"}, h)
    assert r.status_code == 502

    conv = _request("GET", f"{BASE}/conversations/{cid}", headers=h).json()
    assert conv["messages"] == [] and conv["title"] == "New chat"       # retry par duplicate nahi


def test_admin_creates_company_and_user_in_it(monkeypatch, isolate):
    monkeypatch.setattr(container, "api_keys", parse_api_keys(f"root:{ROOT_KEY}:admin"))
    admin = {"Authorization": f"Bearer {ROOT_KEY}"}
    assert _request("POST", f"{BASE}/admin/companies", {"slug": "Bad Slug", "name": "x"}, admin).status_code == 422
    made = _request("POST", f"{BASE}/admin/companies", {"slug": "newco", "name": "New Co"}, admin)
    assert made.status_code == 201 and made.json()["slug"] == "newco"
    assert _request("POST", f"{BASE}/admin/companies", {"slug": "newco", "name": "Again"}, admin).status_code == 400
    u = _request("POST", f"{BASE}/admin/users",
                 {"username": "carol", "password": PASSWORD, "company": "newco", "role": "company_admin"}, admin)
    assert u.status_code == 201 and u.json()["role"] == "company_admin"


def test_chat_request_validation(isolate):
    _user(isolate, "alice")
    h = _bearer("alice")
    cid = _new_conversation(h)
    url = f"{BASE}/conversations/{cid}/chat"
    assert _request("POST", url, {}, h).status_code == 422
    assert _request("POST", url, {"message": "a", "regenerate": True}, h).status_code == 422
