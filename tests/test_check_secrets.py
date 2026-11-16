import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("check_secrets", Path(__file__).resolve().parent.parent / "scripts" / "check_secrets.py")
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)


def test_scanner_finds_planted_secrets_and_ignores_placeholders(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "ROOT", tmp_path)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "bad.py").write_text('API_KEY = "sk_live_a1b2c3d4e5f6g7h8i9"\n')
    (tmp_path / "app" / "url.py").write_text("DB=postgresql://admin:Sup3rS3cret@10.0.0.5/db\n")
    (tmp_path / "key.txt").write_text("-----BEGIN " + "RSA PRIVATE KEY-----\n")   # split: scanner apni hi file na pakde
    (tmp_path / "ok.env").write_text("POSTGRES_PASSWORD=<CHANGE_ME_alphanumeric_only>\nDATABASE_URL=postgresql://company_ai:${POSTGRES_PASSWORD}@postgres/db\n")
    (tmp_path / "app" / "fine.py").write_text("token = secrets.token_urlsafe(32)\n")
    kinds = {(rel, kind) for rel, _, kind in cs.scan(True)}
    assert ("app/bad.py", "secret-like assignment") in kinds
    assert ("app/url.py", "DB URL with password") in kinds
    assert ("key.txt", "private key") in kinds
    assert not any(rel in ("ok.env", "app/fine.py") for rel, _ in kinds)


def test_dotenv_file_is_always_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("A=1\n")
    assert any("environment file" in k for _, _, k in cs.scan(True))
