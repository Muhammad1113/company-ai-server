"""RagService async store (pgvector jaisa) ke saath bhi wahi kaam kare."""
import asyncio

from app.rag.embeddings import HashingEmbedder
from app.rag.service import RagService
from app.rag.store import JsonVectorStore


class AsyncStore:
    """JsonVectorStore ko async interface mein wrap (pgvector jaisa)."""
    def __init__(self, embedder_id):
        self._s = JsonVectorStore(None, embedder_id)
        self.calls = []

    async def replace_document(self, doc_id, collection, items):
        self.calls.append("replace"); return self._s.replace_document(doc_id, collection, items)

    async def search(self, vector, k, collection):
        self.calls.append("search"); return self._s.search(vector, k, collection)

    async def delete_document(self, doc_id, collection):
        self.calls.append("delete"); return self._s.delete_document(doc_id, collection)

    async def stats(self):
        self.calls.append("stats"); return self._s.stats()


def test_service_works_with_async_store():
    async def run():
        emb = HashingEmbedder(256)
        store = AsyncStore(emb.id)

        async def gen(message, temperature, max_tokens):
            return {"choices": [{"message": {"content": "ok [1]"}}], "usage": None}

        svc = RagService(emb, store, gen, top_k=2)
        await svc.ingest("refund", "Refunds are possible within 30 days of purchase.", "fin")
        out = await svc.query("refund within 30 days", "fin")

        assert out["sources"][0]["doc_id"] == "refund" and out["llm_called"]
        assert (await svc.stats())["chunks"] == 1
        assert await svc.delete("refund", "fin") == 1
        assert store.calls == ["replace", "search", "stats", "delete"]
    asyncio.run(run())
