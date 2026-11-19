r"""
Data ka backup (SQLite online backup: server chalte hue bhi consistent) + RAG JSON stores + reg_archive.
PostgreSQL ka backup alag: scripts\\pg_backup.ps1 (aur restore test: scripts\\pg_restore_test.ps1).

  python scripts\backup.py                 -> backups\YYYYmmdd-HHMMSS\
  python scripts\backup.py --keep 30

Roz chalane ke liye (Windows Task Scheduler), deploy\README.md dekho.
"""
import argparse
import shutil
import sqlite3
import time
from pathlib import Path


def backup(data_dir="data", dest="backups", keep=14) -> Path:
    data, root = Path(data_dir), Path(dest)
    root.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out, n = root / stamp, 0
    while out.exists():
        n += 1
        out = root / f"{stamp}-{n:03d}"            # zero-padded: sorting hamesha time-order mein
    out.mkdir()

    for db in data.glob("*.db"):
        src = sqlite3.connect(db)
        dst = sqlite3.connect(out / db.name)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

    for path in data.glob("*.json"):
        shutil.copy2(path, out / path.name)

    for sub in ("reg_archive",):                      # regulatory purane versions ki asli files (DB mein sirf record hai)
        if (data / sub).is_dir():
            shutil.copytree(data / sub, out / sub)
    prompt = data / "system_prompt.txt"
    if prompt.is_file():
        shutil.copy2(prompt, out / prompt.name)

    for old in sorted(p for p in root.iterdir() if p.is_dir())[:-keep]:
        shutil.rmtree(old, ignore_errors=True)
        if old.exists():       # Windows: koi process file khole hue ho to chup-chaap na rahe
            print(f"WARNING: could not delete old backup {old} (file in use?)")

    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--dest", default="backups")
    ap.add_argument("--keep", type=int, default=14)
    args = ap.parse_args()
    print("Backup saved to", backup(args.data, args.dest, args.keep))
