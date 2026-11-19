r"""
Repo mein secrets dhoondo (password, API key, private key, DB URL with password). GitHub par push karne se PEHLE chalao.

  python scripts\check_secrets.py            (git ho to tracked files, warna poora folder)
  python scripts\check_secrets.py --all      (poora folder: .env jaisi untracked files bhi)

Exit code 1 = kuch shak wala mila. Secret ki value kabhi print nahi hoti (sirf file:line aur kism).
Ye gitleaks/trufflehog ka badal nahi: git HISTORY ke liye wohi chalao (docs/PRODUCTION_CHECKLIST.md).
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "dist", "data", "backups", "logs", "models",
             "__pycache__", ".pytest_cache"}
SKIP_SUFFIX = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".gguf", ".db", ".pyc", ".zip", ".pdf", ".ico"}
MAX_BYTES = 1_000_000

PLACEHOLDER = re.compile(r"(<[^>]+>|user:pass|:password@|\$\{?[A-Za-z_]+\}?|change_?me|your[_-]|example|xxxx|placeholder|\*{3,}|\.\.\.)", re.I)

RULES = [
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY"), True),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), True),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), True),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), True),
    ("DB URL with password", re.compile(r"postgres(?:ql)?://[^:/\s@]+:([^@\s]{4,})@"), False),
    ("secret-like assignment",
     re.compile(r"""(?i)\b(?:api[_-]?key|secret|token|passwd|password|pwd)\w*\s*[:=]\s*['"]?([A-Za-z0-9_\-/+=.]{16,})"""),
     False),
]
# tests / docs mein banawati passwords hote hain: sirf pakke patterns (private key, AWS...) wahan dekhte hain
LOOSE_SKIP_PARTS = {"tests", "docs", "eval", "frontend/test"}


def _files(all_files: bool):
    if not all_files and (ROOT / ".git").exists():
        try:
            out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
            return [ROOT / line for line in out.splitlines() if line]
        except (OSError, subprocess.CalledProcessError):
            pass
    return [p for p in ROOT.rglob("*") if p.is_file() and not (set(p.relative_to(ROOT).parts[:-1]) & SKIP_DIRS)]


def scan(all_files: bool = False) -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    for path in _files(all_files):
        rel = path.relative_to(ROOT).as_posix()
        if path.suffix.lower() in SKIP_SUFFIX or not path.is_file() or path.stat().st_size > MAX_BYTES:
            continue
        if path.name == ".env" or (path.name.startswith(".env.") and "example" not in path.name):
            findings.append((rel, 0, "environment file present (must never be committed)"))
            continue
        loose_ok = not any(rel == p or rel.startswith(p + "/") for p in LOOSE_SKIP_PARTS)
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for no, line in enumerate(lines, 1):
            for name, rx, strict in RULES:
                if not strict and not loose_ok:
                    continue
                m = rx.search(line)
                if not m:
                    continue
                value = m.group(1) if m.groups() else m.group(0)
                if not strict and PLACEHOLDER.search(line):
                    continue
                if name == "secret-like assignment" and not any(ch.isdigit() for ch in value):
                    continue          # sirf code identifier (secrets.token_urlsafe jaisa), asli secret mein digit hota hai
                if "sys.argv" in line or "getpass" in line or "os.environ" in line or "settings." in line:
                    continue
                findings.append((rel, no, name))
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="poora folder (untracked files bhi)")
    args = ap.parse_args()
    findings = scan(args.all)
    gi = ROOT / ".gitignore"
    if not gi.exists() or ".env" not in gi.read_text(encoding="utf-8").split():
        findings.append((".gitignore", 0, ".gitignore missing or does not list .env"))
    for rel, no, kind in findings:
        print(f"[FOUND] {rel}{':' + str(no) if no else ''}  {kind}")
    if not findings:
        print("OK: nothing suspicious found.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
