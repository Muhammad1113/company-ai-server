"""eCFR (21 CFR) sync with admin approval + router rule: FDA/regulation questions search reg__* documents first."""
import asyncio

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
from app.services import container, ecfr, openfda  # noqa: E402
from app.services.regulatory import RegulatoryService  # noqa: E402
from app.services.router import docs_min_score, regulatory_signal  # noqa: E402

P = "/api/v1/admin/regulatory"
ROOT = {"Authorization": "Bearer root-admin-secret-key-001"}
USER = {"Authorization": "Bearer plain-user-secret-key-0001"}


def run(coro):
    return asyncio.run(coro)


def part_xml(sections: dict[str, str], appendix: str | None = None) -> bytes:
    body = "".join(
        f'<DIV8 N="&#167; {n}" TYPE="SECTION"><HEAD>&#167; {n} {t.split("|")[0]}</HEAD><P>{t.split("|")[1]}</P></DIV8>'
        for n, t in sections.items())
    if appendix:
        body += f'<DIV9 N="Appendix A" TYPE="APPENDIX"><HEAD>Appendix A</HEAD><P>{appendix}</P></DIV9>'
    return f'<?xml version="1.0"?><DIV1 N="21" TYPE="TITLE"><DIV5 N="820" TYPE="PART">{body}</DIV5></DIV1>'.encode()


class FakeECFR:
    def __init__(self):
        self.parts = {}
        self.as_of = "2026-11-10"
        self.status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.status != 200:
            return httpx.Response(self.status, text="error")
        if request.url.path == "/api/versioner/v1/titles.json":
            return httpx.Response(200, json={"titles": [{"number": 20, "up_to_date_as_of": "2000-01-01"},
                                                        {"number": 21, "up_to_date_as_of": self.as_of}]})
        assert request.url.path == f"/api/versioner/v1/full/{self.as_of}/title-21.xml"
        xml = self.parts.get(request.url.params["part"])
        return httpx.Response(200, content=xml) if xml else httpx.Response(404)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    fake = FakeECFR()
    embedder = HashingEmbedder(256)
    rag = RagService(embedder, JsonVectorStore(None, embedder.id), None)
    repo = RegulatoryRepository(":memory:")
    run(repo.init())
    ecfr_client = ecfr.ECFRClient("https://www.ecfr.gov", 21, delay=0, transport=httpx.MockTransport(fake.handler))
    fda_client = openfda.OpenFDAClient("https://api.fda.gov", delay=0,
                                       transport=httpx.MockTransport(lambda r: httpx.Response(404, json={})))
    svc = RegulatoryService(repo, rag, str(tmp_path / "archive"), fda_client, ecfr_client=ecfr_client, max_parts=3)
    accounts = AccountsRepository(":memory:", scrypt_n=2 ** 10)
    run(accounts.init())
    monkeypatch.setattr(container, "accounts_repo", accounts)
    monkeypatch.setattr(container, "api_keys", parse_api_keys(
        "root:root-admin-secret-key-001:admin,bob:plain-user-secret-key-0001:user"))
    monkeypatch.setattr(container, "rate_limiter", None)
    monkeypatch.setattr(container, "regulatory_repo", repo)
    monkeypatch.setattr(container, "regulatory_service", svc)
    monkeypatch.setattr(container, "rag_service", rag)
    return type("Env", (), {"fake": fake, "svc": svc, "repo": repo, "rag": rag, "client": TestClient(app)})


def seed(env):
    env.fake.parts["820"] = part_xml({
        "820.1": "Scope.|This part applies to manufacturers of finished devices.",
        "820.10": "Requirements.|Manufacturers must establish a quality management system.",
        "820.100": "Corrective action.|Manufacturers must analyze nonconformities."}, appendix="Appendix table text")


def test_default_parts_seeded_once_and_part_validation(env):
    st = env.client.get(f"{P}/ecfr", headers=ROOT).json()
    assert [p["part"] for p in st["parts"]] == ["801", "807", "820", "878"] and st["title"] == 21
    assert env.client.delete(f"{P}/ecfr/parts/807", headers=ROOT).status_code == 200
    again = env.client.get(f"{P}/ecfr", headers=ROOT).json()
    assert [p["part"] for p in again["parts"]] == ["801", "820", "878"]          # hataya hua dobara nahi aata
    assert env.client.post(f"{P}/ecfr/parts", json={"part": "abc"}, headers=ROOT).status_code == 422
    assert env.client.post(f"{P}/ecfr/parts", json={"part": "803"}, headers=ROOT).status_code == 400   # max_parts=3
    assert env.client.delete(f"{P}/ecfr/parts/999", headers=ROOT).status_code == 404


def test_sync_creates_pending_and_nothing_enters_rag_until_approved(env):
    seed(env)
    env.client.get(f"{P}/ecfr", headers=ROOT)
    for part in ("801", "807", "878"):
        env.client.delete(f"{P}/ecfr/parts/{part}", headers=ROOT)
    res = env.client.post(f"{P}/ecfr/sync", headers=ROOT).json()
    assert res["changes"] == 4 and res["errors"] == [] and res["as_of"] == "2026-11-10"
    assert run(env.rag.search("quality management system", "reg__fda", 5)) == []         # approval se pehle kuch nahi

    pend = env.client.get(f"{P}/ecfr/pending", headers=ROOT).json()["updates"]
    assert len(pend) == 1 and pend[0]["source"] == "ecfr" and pend[0]["summary"]["by_code"] == {
        "21 CFR 820": {"new": 4, "changed": 0, "removed": 0}}
    assert env.client.get(f"{P}/openfda/pending", headers=ROOT).json()["updates"] == []   # openFDA list alag

    ok = env.client.post(f"{P}/openfda/pending/{res['pending_id']}/approve", headers=ROOT)
    assert ok.status_code == 200 and ok.json()["documents"] == ["ecfr-21cfr820"]
    texts = " ".join(h["text"] for h in run(env.rag.search("establish a quality management system", "reg__fda", 5)))
    assert "21 CFR 820.10" in texts and "quality management system" in texts and "current as of 2026-11-10" in texts


def _only_820(env):
    env.client.get(f"{P}/ecfr", headers=ROOT)
    for part in ("801", "807", "878"):
        env.client.delete(f"{P}/ecfr/parts/{part}", headers=ROOT)


def _approved_first_sync(env):
    seed(env)
    _only_820(env)
    first = env.client.post(f"{P}/ecfr/sync", headers=ROOT).json()
    env.client.post(f"{P}/openfda/pending/{first['pending_id']}/approve", headers=ROOT)
    assert env.client.post(f"{P}/ecfr/sync", headers=ROOT).json()["changes"] == 0         # kuch nahi badla
    env.fake.parts["820"] = part_xml({
        "820.1": "Scope.|This part applies to manufacturers of finished devices.",
        "820.10": "Requirements.|Manufacturers must establish and maintain a quality management system.",
        "820.200": "New section.|Brand new requirement text."}, appendix="Appendix table text")


def test_section_diff_new_changed_removed_and_approve(env):
    _approved_first_sync(env)
    second = env.client.post(f"{P}/ecfr/sync", headers=ROOT).json()
    assert second["changes"] == 3
    detail = env.client.get(f"{P}/openfda/pending/{second['pending_id']}", headers=ROOT).json()
    assert sorted(c["change"] for c in detail["payload"]) == ["changed", "new", "removed"]
    changed = [c for c in detail["payload"] if c["change"] == "changed"][0]
    assert [f["field"] for f in changed["fields"]] == ["text"]

    assert env.client.post(f"{P}/openfda/pending/{second['pending_id']}/approve", headers=ROOT).status_code == 200
    brand_new = " ".join(h["text"] for h in run(env.rag.search("Brand new requirement text", "reg__fda", 8)))
    assert "Brand new" in brand_new
    gone = " ".join(h["text"] for h in run(env.rag.search("corrective action nonconformities", "reg__fda", 8)))
    assert "nonconformities" not in gone                                                   # hata hua section index se bhi gaya


def test_rejected_section_changes_are_not_reproposed(env):
    _approved_first_sync(env)
    second = env.client.post(f"{P}/ecfr/sync", headers=ROOT).json()
    assert env.client.post(f"{P}/openfda/pending/{second['pending_id']}/reject", headers=ROOT).status_code == 200
    assert env.client.post(f"{P}/ecfr/sync", headers=ROOT).json()["changes"] == 0         # reject yaad hai
    texts = " ".join(h["text"] for h in run(env.rag.search("Brand new requirement text", "reg__fda", 8)))
    assert "Brand new" not in texts                                                        # approve nahi hua => index mein nahi


def test_ecfr_pending_does_not_supersede_openfda_pending(env):
    pid = run(env.repo.create_pending({"total": 1, "new": 1, "changed": 0, "by_code": {"GAD": {"new": 1, "changed": 0}}}, [],
                                      source="openfda"))
    seed(env)
    env.client.get(f"{P}/ecfr", headers=ROOT)
    env.client.post(f"{P}/ecfr/sync", headers=ROOT)
    assert [p["id"] for p in env.client.get(f"{P}/openfda/pending", headers=ROOT).json()["updates"]] == [pid]


def test_ecfr_errors_are_reported_not_raised(env):
    seed(env)
    env.client.get(f"{P}/ecfr", headers=ROOT)
    env.fake.status = 500
    res = env.client.post(f"{P}/ecfr/sync", headers=ROOT).json()
    assert res["changes"] == 0 and res["errors"] and res["pending_id"] is None
    env.fake.status = 200
    res = env.client.post(f"{P}/ecfr/sync", headers=ROOT).json()                          # 801/807/878 FakeECFR mein nahi => 404
    assert res["changes"] == 4 and {e["code"] for e in res["errors"]} == {"801", "807", "878"}


def test_ecfr_routes_are_super_admin_only(env):
    for method, path in [("get", "/ecfr"), ("post", "/ecfr/sync"), ("get", "/ecfr/pending"),
                         ("post", "/ecfr/parts"), ("delete", "/ecfr/parts/820")]:
        assert getattr(env.client, method)(P + path, headers=USER).status_code == 403
        assert getattr(env.client, method)(P + path).status_code == 401


# ---------------------------------------------------------------- router rule
def test_regulatory_questions_are_detected_and_not_pushed_to_internet():
    for q in ["abhi wase fda k law mai koi strict point hai?", "current FDA QMSR requirements",
              "acha abhi fda k kon kon se instrument hai list du", "21 CFR 820 kya kehta hai"]:
        assert regulatory_signal(q), q
        assert docs_min_score(q, 0.4) == 0.4, q                      # "abhi/current" se bar uncha nahi
    assert docs_min_score("check online FDA law", 0.4) is None       # user khud internet maange to wohi
    assert not regulatory_signal("who is current cm of KP")
    assert docs_min_score("who is current cm of KP", 0.4) == 0.4 + 0.15
