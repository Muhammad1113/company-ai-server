"""Phase 3: versioned regulatory documents (archive/restore) + openFDA sync with admin approval."""
import asyncio
import io
import json
import tempfile
from pathlib import Path

import httpx
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from app.core.security import parse_api_keys  # noqa: E402
from app.database.accounts import AccountsRepository  # noqa: E402
from app.database.regulatory import RegulatoryRepository  # noqa: E402
from app.main import app  # noqa: E402
from app.rag.embeddings import HashingEmbedder  # noqa: E402
from app.rag.service import RagService  # noqa: E402
from app.rag.store import JsonVectorStore  # noqa: E402
from app.services import container, openfda  # noqa: E402
from app.services.regulatory import RegulatoryError, RegulatoryService  # noqa: E402

P = "/api/v1/admin/regulatory"
ROOT = {"Authorization": "Bearer root-admin-secret-key-001"}
USER = {"Authorization": "Bearer plain-user-secret-key-0001"}


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- fake openFDA
class FakeFDA:
    """Mutable fake: records[(kind, code)] = list of raw dicts."""

    def __init__(self):
        self.records = {}
        self.status = 200
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"code": "X"}})
        kind = {"/device/classification.json": "classification", "/device/510k.json": "510k",
                "/device/recall.json": "recall"}[request.url.path]
        code = request.url.params["search"].split('"')[1]
        rows = self.records.get((kind, code))
        if not rows:
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "No matches found!"}})
        return httpx.Response(200, json={"results": rows})


def make_service(tmp: Path, fda: FakeFDA):
    embedder = HashingEmbedder(256)
    rag = RagService(embedder, JsonVectorStore(None, embedder.id), None)
    repo = RegulatoryRepository(":memory:")
    run(repo.init())
    client = openfda.OpenFDAClient("https://api.fda.gov", limit=5, delay=0,
                                   transport=httpx.MockTransport(fda.handler))
    return RegulatoryService(repo, rag, str(tmp / "archive"), client, max_codes=3), repo, rag


@pytest.fixture()
def env(tmp_path, monkeypatch):
    fda = FakeFDA()
    svc, repo, rag = make_service(tmp_path, fda)
    accounts = AccountsRepository(":memory:", scrypt_n=2 ** 10)
    run(accounts.init())
    monkeypatch.setattr(container, "accounts_repo", accounts)
    monkeypatch.setattr(container, "api_keys", parse_api_keys(
        "root:root-admin-secret-key-001:admin,bob:plain-user-secret-key-0001:user"))
    monkeypatch.setattr(container, "rate_limiter", None)
    monkeypatch.setattr(container, "regulatory_repo", repo)
    monkeypatch.setattr(container, "regulatory_service", svc)
    monkeypatch.setattr(container, "rag_service", rag)
    return type("Env", (), {"fda": fda, "svc": svc, "repo": repo, "rag": rag, "tmp": tmp_path,
                            "client": TestClient(app)})


def upload(env, version, text, *, key="eu-mdr", collection="reg__eu", filename="mdr.txt", extra="", headers=ROOT):
    url = f"{P}/versions?filename={filename}&collection={collection}&doc_key={key}&version={version}{extra}"
    return env.client.post(url, content=text.encode(), headers=headers)


def hits(env, q, collection="reg__eu"):
    return run(env.rag.search(q, collection, 5))


# ---------------------------------------------------------------- versioned documents
def test_new_version_archives_old_and_index_has_only_active(env):
    r1 = upload(env, "2024-01", "MDR rule: sterile gloves require notified body review alpha.", extra="&effective_date=2024-01-01")
    assert r1.status_code == 201 and r1.json()["status"] == "active"
    r2 = upload(env, "2026-05", "MDR rule: sterile gloves require notified body review omega.")
    assert r2.status_code == 201

    versions = {v["version"]: v for v in env.client.get(f"{P}/versions", headers=ROOT).json()["versions"]}
    assert versions["2026-05"]["status"] == "active" and versions["2024-01"]["status"] == "archived"

    texts = " ".join(h["text"] for h in hits(env, "sterile gloves notified body"))
    assert "omega" in texts and "alpha" not in texts                       # purana version jawab mein nahi
    assert "version 2026-05" in texts                                       # header se version jawab mein dikhta hai
    # archive mein asli file + text dono
    folder = env.tmp / "archive" / "reg__eu" / "eu-mdr" / "2024-01"
    assert (folder / "mdr.txt").is_file() and "alpha" in (folder / "extracted.txt").read_text()


def test_restore_swaps_active_and_retire_removes_from_index(env):
    a = upload(env, "v1", "Rule text first edition marker1111.").json()
    b = upload(env, "v2", "Rule text second edition marker2222.").json()
    restored = env.client.post(f"{P}/versions/{a['id']}/restore", headers=ROOT)
    assert restored.status_code == 200 and restored.json()["status"] == "active"
    texts = " ".join(h["text"] for h in hits(env, "rule text edition"))
    assert "marker1111" in texts and "marker2222" not in texts
    assert run(env.repo.get_by_id(b["id"]))["status"] == "archived"
    assert env.client.post(f"{P}/versions/{a['id']}/restore", headers=ROOT).status_code == 400   # already active

    retired = env.client.post(f"{P}/versions/{a['id']}/retire", headers=ROOT)
    assert retired.status_code == 200 and retired.json()["status"] == "retired"
    assert hits(env, "rule text edition") == []
    assert env.client.post(f"{P}/versions/9999/restore", headers=ROOT).status_code == 404


def test_version_validation_and_archive_only(env):
    assert upload(env, "v1", "x text", collection="reg__eu").status_code == 201
    assert upload(env, "v1", "other text").status_code == 400                          # duplicate version
    assert upload(env, "bad version!", "t").status_code in (400, 422)
    assert upload(env, "v3", "t", extra="&effective_date=05/01/2026").status_code == 400
    assert upload(env, "v3", "t", extra="&source_url=javascript:alert(1)").status_code == 400
    assert upload(env, "v3", "t", key="../../etc").status_code == 400                  # path escape
    # company collection par versioned upload nahi
    assert env.client.post(f"{P}/versions?filename=a.txt&collection=co_acme__docs&doc_key=k&version=v",
                           content=b"hello", headers=ROOT).status_code == 422
    # archive-only: index mein nahi aata, active bhi nahi badalta
    assert upload(env, "v0", "ancient edition marker0000", extra="&archive_only=true").json()["status"] == "archived"
    assert all("marker0000" not in h["text"] for h in hits(env, "ancient edition"))
    assert run(env.repo.get_active("reg__eu", "eu-mdr"))["version"] == "v1"


def test_regulatory_routes_are_super_admin_only(env):
    for method, path in [("get", "/versions"), ("get", "/openfda"), ("post", "/openfda/sync"),
                         ("get", "/openfda/pending"), ("post", "/openfda/pending/1/approve")]:
        assert getattr(env.client, method)(P + path, headers=USER).status_code == 403
        assert getattr(env.client, method)(P + path).status_code == 401


# ---------------------------------------------------------------- openFDA
def fda_data(env):
    env.fda.records[("510k", "GAD")] = [
        {"k_number": "K100001", "device_name": "Forceps A", "applicant": "Acme Surgical", "decision_date": "2025-01-10",
         "decision_description": "Substantially Equivalent", "product_code": "GAD", "junk_field": "ignored"},
        {"k_number": "K100002", "device_name": "Forceps B", "applicant": "Beta Inc", "decision_date": "2025-02-10",
         "product_code": "GAD"},
    ]
    env.fda.records[("classification", "GAD")] = [
        {"product_code": "GAD", "device_name": "Forceps, general", "device_class": "1", "regulation_number": "878.4800"}]


def test_sync_creates_pending_and_nothing_enters_rag_until_approved(env):
    fda_data(env)
    assert env.client.post(f"{P}/openfda/codes", json={"code": "gad", "label": "Forceps"}, headers=ROOT).status_code == 201
    assert env.client.post(f"{P}/openfda/codes", json={"code": "TOOLONG"}, headers=ROOT).status_code == 422
    res = env.client.post(f"{P}/openfda/sync", headers=ROOT).json()
    assert res["changes"] == 3 and res["pending_id"] and res["errors"] == []

    assert run(env.rag.search("forceps", "reg__fda", 5)) == []                       # approval se pehle kuch nahi
    pend = env.client.get(f"{P}/openfda/pending", headers=ROOT).json()["updates"]
    assert len(pend) == 1 and pend[0]["summary"]["new"] == 3
    detail = env.client.get(f"{P}/openfda/pending/{res['pending_id']}", headers=ROOT).json()
    assert all("junk_field" not in c["data"] for c in detail["payload"])             # sirf whitelisted fields

    ok = env.client.post(f"{P}/openfda/pending/{res['pending_id']}/approve", headers=ROOT)
    assert ok.status_code == 200 and "openfda-gad-510k" in ok.json()["documents"]
    texts = " ".join(h["text"] for h in run(env.rag.search("Forceps A Acme Surgical K100001", "reg__fda", 5)))
    assert "K100001" in texts and "Acme Surgical" in texts
    assert env.client.post(f"{P}/openfda/pending/{res['pending_id']}/approve", headers=ROOT).status_code == 400  # double approve


def test_second_sync_only_proposes_changes_and_rejected_not_reproposed(env):
    fda_data(env)
    env.client.post(f"{P}/openfda/codes", json={"code": "GAD"}, headers=ROOT)
    first = env.client.post(f"{P}/openfda/sync", headers=ROOT).json()
    env.client.post(f"{P}/openfda/pending/{first['pending_id']}/approve", headers=ROOT)

    assert env.client.post(f"{P}/openfda/sync", headers=ROOT).json()["changes"] == 0   # kuch nahi badla

    env.fda.records[("510k", "GAD")][0]["decision_description"] = "Withdrawn"
    env.fda.records[("510k", "GAD")].append({"k_number": "K100003", "device_name": "Forceps C", "product_code": "GAD"})
    second = env.client.post(f"{P}/openfda/sync", headers=ROOT).json()
    assert second["changes"] == 2
    detail = env.client.get(f"{P}/openfda/pending/{second['pending_id']}", headers=ROOT).json()
    changed = [c for c in detail["payload"] if c["change"] == "changed"][0]
    assert changed["fields"] == [{"field": "decision_description", "old": "Substantially Equivalent", "new": "Withdrawn"}]

    assert env.client.post(f"{P}/openfda/pending/{second['pending_id']}/reject", headers=ROOT).status_code == 200
    assert env.client.post(f"{P}/openfda/sync", headers=ROOT).json()["changes"] == 0   # reject yaad hai
    texts = " ".join(h["text"] for h in run(env.rag.search("Withdrawn K100003", "reg__fda", 8)))
    assert "Withdrawn" not in texts and "K100003" not in texts

    env.fda.records[("510k", "GAD")][0]["decision_description"] = "Cleared again"        # naya hash => dobara propose
    assert env.client.post(f"{P}/openfda/sync", headers=ROOT).json()["changes"] == 1


def test_new_sync_supersedes_old_pending_and_errors_reported(env):
    fda_data(env)
    env.client.post(f"{P}/openfda/codes", json={"code": "GAD"}, headers=ROOT)
    a = env.client.post(f"{P}/openfda/sync", headers=ROOT).json()
    b = env.client.post(f"{P}/openfda/sync", headers=ROOT).json()
    assert a["pending_id"] != b["pending_id"]
    assert [p["id"] for p in env.client.get(f"{P}/openfda/pending", headers=ROOT).json()["updates"]] == [b["pending_id"]]
    assert env.client.post(f"{P}/openfda/pending/{a['pending_id']}/approve", headers=ROOT).status_code == 400

    env.fda.status = 429
    res = env.client.post(f"{P}/openfda/sync", headers=ROOT).json()
    assert res["errors"] and "rate limit" in res["errors"][0]["error"]
    status = env.client.get(f"{P}/openfda", headers=ROOT).json()
    assert status["pending_count"] == 1 and status["last_run"]["errors"]


def test_code_limits_and_sync_needs_a_code(env):
    assert env.client.post(f"{P}/openfda/sync", headers=ROOT).status_code == 400
    for c in ("AAA", "BBB", "CCC"):
        assert env.client.post(f"{P}/openfda/codes", json={"code": c}, headers=ROOT).status_code == 201
    assert env.client.post(f"{P}/openfda/codes", json={"code": "DDD"}, headers=ROOT).status_code == 400   # max 3 in this test
    assert env.client.delete(f"{P}/openfda/codes/AAA", headers=ROOT).status_code == 200
    assert env.client.delete(f"{P}/openfda/codes/AAA", headers=ROOT).status_code == 404


def test_overview_reports_pending_regulatory_count(env):
    fda_data(env)
    env.client.post(f"{P}/openfda/codes", json={"code": "GAD"}, headers=ROOT)
    env.client.post(f"{P}/openfda/sync", headers=ROOT)
    run(container.usage_repo.init())
    r = env.client.get("/api/v1/admin/overview", headers=ROOT)
    assert r.status_code == 200 and r.json()["pending_regulatory"] == 1


def test_view_document_content_and_archived_version_text(env):
    a = upload(env, "v1", "First edition text marker1111.").json()
    upload(env, "v2", "Second edition text marker2222.")
    r = env.client.get(f"/api/v1/rag/documents/eu-mdr/content?collection=reg__eu", headers=ROOT)
    assert r.status_code == 200 and "marker2222" in r.json()["parts"][0]["text"]
    old = env.client.get(f"{P}/versions/{a['id']}/text", headers=ROOT)
    assert old.status_code == 200 and "marker1111" in old.json()["text"]
    assert env.client.get(f"{P}/versions/9999/text", headers=ROOT).status_code == 404
    assert env.client.get("/api/v1/rag/documents/nope/content?collection=reg__eu", headers=ROOT).status_code == 404
    # plain user: manage nahi => 403; super-admin-only version text
    assert env.client.get("/api/v1/rag/documents/eu-mdr/content?collection=reg__eu", headers=USER).status_code == 403
    assert env.client.get(f"{P}/versions/{a['id']}/text", headers=USER).status_code == 403
