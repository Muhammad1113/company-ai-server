import json
import os
import operator
import threading


def _dot(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError("embedding dimension mismatch; re-ingest documents")
    return sum(map(operator.mul, a, b))


def summarize_metadata(meta: dict | None) -> dict:
    """Document list ke liye: upload ke waqt ka metadata (type, pages, chars) ko saaf shakl mein."""
    meta = meta or {}
    out: dict = {"type": meta.get("type") or None}
    for key in ("pages", "chars"):
        raw = meta.get(key)
        out[key] = int(raw) if str(raw).isdigit() else None
    return out


class JsonVectorStore:
    """
    Chhota brute-force cosine store (vectors normalized => dot product).
    Disk par JSON (atomic write). Qdrant/Chroma tak isi interface se jao:
    replace_document / search / delete_document / stats.
    """

    def __init__(self, path: str | None, embedder_id: str):
        self.path = path
        self.embedder_id = embedder_id
        self._lock = threading.RLock()
        self._items: list[dict] = []
        self._load()

    def _load(self):
        if not self.path or not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("embedder") != self.embedder_id:
            raise ValueError(
                f"RAG store was built with '{data.get('embedder')}' but "
                f"current embedder is '{self.embedder_id}'. "
                "Delete the store file and re-ingest."
            )
        self._items = data["items"]

    def _persist(self):
        if not self.path:
            return
        folder = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(folder, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"embedder": self.embedder_id, "items": self._items}, f)
        os.replace(tmp, self.path)

    def replace_document(self, doc_id, collection, items: list[dict]) -> int:
        """items: [{text, vector, metadata, chunk_index}]. Re-ingest idempotent."""
        with self._lock:
            self._items = [
                it for it in self._items
                if not (it["doc_id"] == doc_id and it["collection"] == collection)
            ]
            for it in items:
                self._items.append({
                    "doc_id": doc_id,
                    "collection": collection,
                    "chunk_index": it["chunk_index"],
                    "text": it["text"],
                    "metadata": it.get("metadata", {}),
                    "vector": [round(x, 5) for x in it["vector"]],
                })
            self._persist()
        return len(items)

    def search(self, vector, k, collection) -> list[dict]:
        with self._lock:
            candidates = [it for it in self._items if it["collection"] == collection]
            scored = [(_dot(vector, it["vector"]), it) for it in candidates]

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {
                "score": round(score, 4),
                "doc_id": it["doc_id"],
                "collection": it["collection"],
                "chunk_index": it["chunk_index"],
                "text": it["text"],
                "metadata": it["metadata"],
            }
            for score, it in scored[:k]
        ]

    def delete_document(self, doc_id, collection) -> int:
        with self._lock:
            before = len(self._items)
            self._items = [
                it for it in self._items
                if not (it["doc_id"] == doc_id and it["collection"] == collection)
            ]
            removed = before - len(self._items)
            if removed:
                self._persist()
        return removed

    def list_documents(self, collection) -> list[dict]:
        """[{doc_id, chunks, type, pages, chars}] (naam ke hisaab se)."""
        with self._lock:
            docs: dict[str, dict] = {}
            for it in self._items:
                if it["collection"] != collection:
                    continue
                d = docs.setdefault(it["doc_id"], {"chunks": 0, "meta": it.get("metadata") or {}})
                d["chunks"] += 1
        return [
            {"doc_id": doc_id, "chunks": d["chunks"], **summarize_metadata(d["meta"])}
            for doc_id, d in sorted(docs.items(), key=lambda kv: kv[0].lower())
        ]

    def get_document_chunks(self, doc_id, collection) -> list[str]:
        """Admin "View": ek document ke chunk texts, chunk_index ke hisaab se."""
        with self._lock:
            rows = [it for it in self._items if it["doc_id"] == doc_id and it["collection"] == collection]
        return [it["text"] for it in sorted(rows, key=lambda it: it["chunk_index"])]

    def stats(self) -> dict:
        with self._lock:
            collections: dict[str, set] = {}
            for it in self._items:
                collections.setdefault(it["collection"], set()).add(it["doc_id"])
            return {
                "embedder": self.embedder_id,
                "chunks": len(self._items),
                "collections": {c: len(d) for c, d in collections.items()},
            }
