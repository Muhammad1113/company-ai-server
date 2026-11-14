import importlib.util
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("backup", ROOT / "scripts" / "backup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows(db_path):
    # Windows: khula connection file lock rakhta hai => hamesha band karo
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute("SELECT x FROM t").fetchall()


def test_backup_copies_db_and_json_and_prunes():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        data, dest = Path(tmp) / "data", Path(tmp) / "backups"
        data.mkdir()

        source = sqlite3.connect(data / "company_ai.db")      # khula (server chalta hua jaisa)
        try:
            source.execute("PRAGMA journal_mode=WAL")
            source.execute("CREATE TABLE t (x TEXT)")
            source.execute("INSERT INTO t VALUES ('hello')")
            source.commit()
            (data / "rag_store.json").write_text(json.dumps({"items": [1, 2, 3]}))

            backup = _load().backup
            first = backup(str(data), str(dest), keep=2)

            assert _rows(first / "company_ai.db") == [("hello",)]
            assert json.loads((first / "rag_store.json").read_text()) == {"items": [1, 2, 3]}

            backup(str(data), str(dest), keep=2)
            backup(str(data), str(dest), keep=2)               # same second mein bhi unique folder

            kept = sorted(p.name for p in dest.iterdir() if p.is_dir())
            assert len(kept) == 2
            assert first.name not in kept                      # sab se purana prune hua
        finally:
            source.close()
