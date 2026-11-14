"""v6: admin document list (delete button ke liye)."""
import asyncio

from app.rag.store import JsonVectorStore, summarize_metadata


def _items(n, meta):
    return [{"chunk_index": i, "text": f"t{i}", "vector": [1.0, 0.0], "metadata": meta} for i in range(n)]


def test_list_documents_json_store():
    s = JsonVectorStore(None, "e1")
    s.replace_document("sutures", "Surgical", _items(3, {"type": "pdf", "pages": "2", "chars": "900"}))
    s.replace_document("Gloves", "Surgical", _items(2, {"type": "pdf", "chars": "500"}))
    s.replace_document("other", "eval", _items(1, {}))
    docs = s.list_documents("Surgical")
    assert [d["doc_id"] for d in docs] == ["Gloves", "sutures"]            # naam ke hisaab se, case-insensitive
    by = {d["doc_id"]: d for d in docs}
    assert by["sutures"] == {"doc_id": "sutures", "chunks": 3, "type": "pdf", "pages": 2, "chars": 900}
    assert by["Gloves"]["pages"] is None and by["Gloves"]["chars"] == 500
    assert s.list_documents("nope") == []


def test_deleted_document_leaves_list():
    s = JsonVectorStore(None, "e1")
    s.replace_document("a", "c", _items(1, {}))
    s.replace_document("b", "c", _items(1, {}))
    assert s.delete_document("a", "c") == 1
    assert [d["doc_id"] for d in s.list_documents("c")] == ["b"]


def test_summarize_metadata_handles_junk():
    assert summarize_metadata(None) == {"type": None, "pages": None, "chars": None}
    assert summarize_metadata({"pages": "x", "chars": "12", "type": "docx"}) == {"type": "docx", "pages": None, "chars": 12}


def test_pgvector_list_documents_with_fake_pool():
    from app.rag.pgvector_store import PgVectorStore

    class Pool:
        async def fetch(self, sql, *args):
            assert "GROUP BY doc_id" in sql and args == ("Surgical",)
            return [{"doc_id": "a", "chunks": 2, "metadata": '{"type": "pdf", "pages": "3"}'},
                    {"doc_id": "b", "chunks": 1, "metadata": None}]

    class Db:
        pool = Pool()

    store = PgVectorStore.__new__(PgVectorStore)
    store.db = Db()
    docs = asyncio.run(store.list_documents("Surgical"))
    assert docs[0] == {"doc_id": "a", "chunks": 2, "type": "pdf", "pages": 3, "chars": None}
    assert docs[1]["type"] is None
