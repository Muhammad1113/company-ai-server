"""Jawab ki maturity: page numbers, greeting/about-bot par documents nahi, prompt ka structure."""
import asyncio

from app.rag.chunker import chunk_pages
from app.rag.embeddings import HashingEmbedder
from app.rag.service import _PROMPT, RagService
from app.rag.store import JsonVectorStore
from app.services.router import about_bot_signal, chitchat_or_task


def _svc():
    embedder = HashingEmbedder(512)
    store = JsonVectorStore(None, embedder.id)

    async def gen(message, temperature, max_tokens):
        return {"choices": [{"message": {"content": "ok [1]"}}], "usage": None}

    return RagService(embedder, store, gen, top_k=2)


def test_chunk_pages_gives_page_numbers_and_never_spans_pages():
    text = "Registration and listing are required.\fLabeling must show the manufacturer name.\fQuality system rules."
    pieces = chunk_pages(text, size=800, overlap=100)
    assert [p for p, _ in pieces] == [1, 2, 3]
    assert "Labeling" in pieces[1][1] and "Quality" not in pieces[1][1]


def test_chunk_pages_without_page_breaks_has_no_page():
    assert all(p is None for p, _ in chunk_pages("Plain text document.\n\nSecond paragraph."))


def test_ingest_stores_page_and_sources_expose_it():
    async def run():
        svc = _svc()
        await svc.ingest("guide", "Intro page about export.\fLabeling must show the manufacturer name and address.", "c1")
        hits = await svc.search("labeling manufacturer name address", "c1")
        prompt, sources = svc.build_prompt("labeling manufacturer name", hits)
        top = sources[0]
        assert top["page"] == 2 and top["kind"] == "doc"
        assert "page 2" in prompt
        assert len(top["preview"]) > 0
    asyncio.run(run())


def test_prompt_forbids_meta_notes_and_defines_general_label():
    assert 'never write "Note:"' in _PROMPT
    assert 'Never write "General knowledge:"' in _PROMPT
    assert "ONE clear answer" in _PROMPT
    assert "{context}" in _PROMPT and "{question}" in _PROMPT


def test_greetings_and_about_bot_do_not_search_documents():
    for q in ["hi", "Hello!", "salam", "thanks"]:
        assert chitchat_or_task(q) == "greeting"
    for q in ["tm kon hu?", "acha apni expertise batao", "what can you do", "tum kon ho", "who are you"]:
        assert about_bot_signal(q)
    for q in ["What are the FDA labeling requirements under 21 CFR 801?", "fda k law kia khety ha", "our MOQ for forceps"]:
        assert not about_bot_signal(q)


def test_more_greetings_and_followups():
    from app.services.router import is_follow_up, topic_overlap
    for q in ["aoa", "AOA", "Assalamualaikum", "walaikum assalam", "a.o.a", "jazakallah"]:
        assert chitchat_or_task(q) == "greeting", q
    assert is_follow_up("is k sizes mai kia", "humre pas forcep k bare mai kia info ha")
    assert not is_follow_up("what is a scalpel", "humre pas forcep k bare mai kia info ha")
    # khaas lafz na milne par chunk nikal jata hai
    assert not topic_overlap("forcep sizes", "THUNDERBEAT 5 mm, 45 cm Front-actuated Grip Type S applicant Olympus")
    assert topic_overlap("forceps sizes", "Forceps, General & Plastic Surgery; device class 1")
    assert topic_overlap("sizes", "anything here")          # khaas lafz hi nahi => filter nahi
