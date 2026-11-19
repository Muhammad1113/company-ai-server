r"""
Production preflight: .env ki settings check karo, deploy se PEHLE chalao.

  python scripts/preflight.py            (exit code 1 agar koi 'error' ho)
  python scripts/preflight.py --strict   (warning par bhi exit code 1)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.core.production import production_issues  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    issues = production_issues(settings)
    for level, msg in issues:
        print(f"[{level.upper():5}] {msg}")
    if not issues:
        print("OK: no issues found.")
    errors = [i for i in issues if i[0] == "error"]
    return 1 if errors or (args.strict and issues) else 0


if __name__ == "__main__":
    sys.exit(main())
