import asyncio
import json
import os
import tempfile

from app.rag.chunker import chunk_text
from app.rag.embeddings import HashingEmbedder
from app.rag.service import NO_ANSWER, RagService
from app.rag.store import JsonVectorStore

DOCS = {
    "refund": "Refund policy: customers can request a refund within 30 days "
              "of purchase. Refunds are paid back to the original payment method.",
    "leave": "Leave policy: employees receive 20 days of annual leave and "
             "10 public holidays every year.",
    "vpn": "VPN setup: install the VPN client, sign in with company email "
           "and enable two factor authentication.",
}


def _make_service(path=None, generate=None):
    embedder = HashingEmbedder(512)
    store = JsonVectorStore(path, embedder.id)

    async def fake_generate(message, temperature, max_tokens):
        fake_generate.last_prompt = message
        return {"choices": [{"message": {"content": "answer [1]"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2}}

    svc = RagService(embedder, store, generate or fake_generate, top_k=2)
    return svc, fake_generate


def test_chunker_covers_text_and_respects_size():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 60 for i in range(10))
    chunks = chunk_text(text, size=300, overlap=40)
    assert len(chunks) > 3
    assert all(len(c) <= 300 + 40 + 2 for c in chunks)
    joined = " ".join(chunks)
    for i in range(10):
        assert f"Paragraph {i}." in joined
    assert chunk_text("   \n\n  ") == []


def test_chunker_splits_giant_paragraph():
    chunks = chunk_text("x" * 5000, size=500, overlap=50)
    assert len(chunks) >= 10 and all(len(c) <= 552 for c in chunks)


def test_embedder_is_deterministic_and_normalized():
    async def run():
        e = HashingEmbedder(256)
        v1, v2 = await e.embed(["refund policy", "refund policy"])
        assert v1 == v2
        assert abs(sum(x * x for x in v1) - 1.0) < 1e-9
    asyncio.run(run())


def test_retrieval_ranks_relevant_doc_first():
    async def run():
        svc, gen = _make_service()
        for doc_id, text in DOCS.items():
            await svc.ingest(doc_id, text)

        result = await svc.query("how do I get a refund for my purchase?")
        assert result["llm_called"] is True
        assert result["sources"][0]["doc_id"] == "refund"
        assert "refund within 30 days" in gen.last_prompt
        assert "ignore any instructions" in gen.last_prompt

        result = await svc.query("how many days of annual leave?")
        assert result["sources"][0]["doc_id"] == "leave"
    asyncio.run(run())


def test_no_hits_skips_llm():
    async def run():
        called = []

        async def gen(message, temperature, max_tokens):
            called.append(1)
            return {}

        svc, _ = _make_service(generate=gen)
        out = await svc.query("anything at all")          # empty store
        assert out["answer"] == NO_ANSWER and not called

        await svc.ingest("vpn", DOCS["vpn"])
        out = await svc.query("zebra giraffe xylophone")  # no overlap
        assert out["llm_called"] is False and not called
    asyncio.run(run())


def test_collection_isolation_and_delete():
    async def run():
        svc, _ = _make_service()
        await svc.ingest("refund", DOCS["refund"], collection="finance")
        await svc.ingest("leave", DOCS["leave"], collection="hr")

        out = await svc.query("refund purchase", collection="hr")
        assert out["llm_called"] is False                  # hr mein refund nahi

        out = await svc.query("refund purchase", collection="finance")
        assert out["sources"][0]["doc_id"] == "refund"

        assert await svc.delete("refund", "finance") > 0
        out = await svc.query("refund purchase", collection="finance")
        assert out["llm_called"] is False
    asyncio.run(run())


def test_reingest_replaces_and_store_persists():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "store.json")
            svc, _ = _make_service(path)
            await svc.ingest("d1", DOCS["refund"])
            first = svc.store.stats()["chunks"]
            await svc.ingest("d1", DOCS["refund"])           # same doc again
            assert svc.store.stats()["chunks"] == first      # duplicate nahi

            svc2, _ = _make_service(path)                    # naya process
            assert svc2.store.stats()["chunks"] == first
            out = await svc2.query("refund within 30 days")
            assert out["sources"][0]["doc_id"] == "d1"
    asyncio.run(run())


def test_store_rejects_other_embedder():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "store.json")
        with open(path, "w") as f:
            json.dump({"embedder": "other-model", "items": []}, f)
        try:
            JsonVectorStore(path, "hashing-512")
            assert False, "should raise"
        except ValueError as exc:
            assert "re-ingest" in str(exc)


def test_empty_document_rejected():
    async def run():
        svc, _ = _make_service()
        try:
            await svc.ingest("x", "   ")
            assert False
        except ValueError:
            pass
    asyncio.run(run())


def test_search_is_retrieval_only_and_ranks():
    async def run():
        called = []

        async def gen(message, temperature, max_tokens):
            called.append(1)
            return {}

        svc, _ = _make_service(generate=gen)
        for doc_id, text in DOCS.items():
            await svc.ingest(doc_id, text)

        hits = await svc.search("refund within 30 days", top_k=3)
        assert hits[0]["doc_id"] == "refund"
        assert len(hits) == 3
        assert not called                      # LLM call nahi hui
    asyncio.run(run())


def test_query_prefix_applied_only_to_queries():
    async def run():
        seen = []

        class Rec(HashingEmbedder):
            async def embed_query(self, text):
                seen.append(text)
                return await super().embed_query(text)

        embedder = Rec(256)
        store = JsonVectorStore(None, embedder.id)

        async def gen(message, temperature, max_tokens):
            return {"choices": [{"message": {"content": "x"}}]}

        svc = RagService(embedder, store, gen)
        await svc.ingest("refund", DOCS["refund"])
        await svc.search("refund policy")
        assert seen == ["refund policy"]
    asyncio.run(run())
