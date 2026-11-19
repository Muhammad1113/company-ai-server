r"""
Purana JSON RAG store -> pgvector (re-embed nahi hota, vectors seedhe copy).

  python scripts\migrate_json_to_pgvector.py --dry-run
  python scripts\migrate_json_to_pgvector.py --map default=reg__eu --map hr=co_acme__docs

Pehle .env mein DATABASE_URL set ho. Embedder wahi hona chahiye jis se JSON bana tha (warna re-ingest karo).
--map old=new : collection ka naam badal kar copy. Jo collection map nahi hui woh JAISI KI TAISI copy hoti hai
aur sirf super admin ko nazar aati hai (company users ko nahi) - isliye har collection ko co_<company>__* ya
reg__* par map karna behtar hai. Dobara chalana safe hai (document replace hota hai, duplicate nahi banta).
"""
import argparse
import asyncio
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.core import tenancy  # noqa: E402
from app.database.postgres import PostgresDatabase  # noqa: E402
from app.rag.pgvector_store import PgVectorStore  # noqa: E402

_NAME = re.compile(r"^[\w.\-:]+$")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=settings.RAG_STORE_PATH)
    ap.add_argument("--map", action="append", default=[], metavar="OLD=NEW")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    mapping = {}
    for m in args.map:
        old, _, new = m.partition("=")
        if not old or not _NAME.match(new):
            sys.exit(f"Bad --map '{m}' (use old=new)")
        mapping[old] = new

    data = json.loads(Path(args.json).read_text(encoding="utf-8"))
    embedder_id, items = data["embedder"], data["items"]
    if not items:
        sys.exit("JSON store is empty")
    dim = len(items[0]["vector"])

    groups = defaultdict(list)
    for it in items:
        groups[(mapping.get(it["collection"], it["collection"]), it["doc_id"])].append(it)

    print(f"{len(items)} chunks, {len(groups)} documents, embedder={embedder_id}, dim={dim}")
    for (col, doc), chunks in sorted(groups.items()):
        scope = "shared" if tenancy.is_shared(col) else "company" if tenancy.owner_of(col) else "LEGACY (super admin only)"
        print(f"  {col:<28} {doc:<40} {len(chunks):>4} chunks  [{scope}]")
    if args.dry_run:
        return

    if not settings.DATABASE_URL:
        sys.exit("DATABASE_URL is empty")
    db = PostgresDatabase(settings.DATABASE_URL)
    await db.connect()
    try:
        store = PgVectorStore(db, embedder_id, dim, hnsw=settings.RAG_PGVECTOR_HNSW)
        await store.init()
        for (col, doc), chunks in groups.items():
            await store.replace_document(doc, col, [
                {"chunk_index": c["chunk_index"], "text": c["text"], "vector": c["vector"],
                 "metadata": c.get("metadata", {})} for c in chunks
            ])
        print("Done. Ab .env mein RAG_STORE_BACKEND=pgvector karo aur server restart karo.")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
