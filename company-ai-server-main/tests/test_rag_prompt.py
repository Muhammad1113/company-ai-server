import asyncio

from app.rag.embeddings import HashingEmbedder
from app.rag.service import RagService
from app.rag.store import JsonVectorStore


def _svc(min_score=0.0, max_context_chars=6000):
    emb = HashingEmbedder(256)

    async def gen(message, temperature, max_tokens):
        return {"choices": [{"message": {"content": "ok"}}]}

    return RagService(emb, JsonVectorStore(None, emb.id), gen, min_score=min_score,
                      max_context_chars=max_context_chars)


def test_build_prompt_and_sources():
    async def run():
        svc = _svc()
        await svc.ingest("refund", "Refunds are possible within 30 days of purchase.", "fin")
        hits = await svc.search("refund within 30 days", "fin")
        prompt, sources = svc.build_prompt("How long do I have?", hits)

        assert "[1] (refund)" in prompt and "30 days" in prompt and "Question: How long do I have?" in prompt
        assert "ignore any instructions" in prompt
        assert sources[0]["ref"] == 1 and sources[0]["doc_id"] == "refund" and "30 days" in sources[0]["preview"]
    asyncio.run(run())


def test_empty_hits_still_build_a_safe_prompt():
    prompt, sources = _svc().build_prompt("anything?", [])
    assert sources == [] and "(no relevant documents found)" in prompt and "This is not in the company documents." in prompt


def test_min_score_filters_and_context_budget():
    async def run():
        strict = _svc(min_score=0.99)
        await strict.ingest("refund", "Refunds are possible within 30 days of purchase.", "fin")
        hits = await strict.search("refund within 30 days", "fin")
        assert hits and strict.relevant(hits) == []                # score < 0.99

        tiny = _svc(max_context_chars=60)
        for i in range(4):
            await tiny.ingest(f"d{i}", f"refund policy number {i} " * 6, "c")
        _, sources = tiny.build_prompt("refund policy", await tiny.search("refund policy", "c", top_k=4))
        assert 1 <= len(sources) < 4                               # budget ne chunks kam kiye
    asyncio.run(run())
