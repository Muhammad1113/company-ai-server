"""EU MDR retrieval fixes: (1) EU sawal par US FDA kabhi mix nahi, (2) Annex VIII Rule 1..22 alag records/chunks aur
classification sawal par Annex VIII (Rule 6) Annex I labelling se upar. Network / LLM nahi."""
import asyncio
import tempfile
import os

from app.rag.chunker import chunk_text
from app.rag.embeddings import HashingEmbedder
from app.rag.service import RagService
from app.rag.store import JsonVectorStore
from app.services import eurlex as eu
from app.services import regsearch
from app.services.router import (collection_region, explicit_regions, regulatory_regions, regulatory_signal,
                                 scope_regulatory)


def run(coro):
    return asyncio.run(coro)


FILL = " ".join(["The manufacturer shall ensure conformity with the applicable requirements."] * 12)


def rule(num, body, head=None):
    return (f'<table><tr><td><p class="oj-normal">{head or "5.1."}</p></td><td><p class="oj-ti-grseq-1">Rule {num}</p></td></tr></table>'
            f'<p class="oj-normal">{body}</p>')


def mdr_html():
    return f'''<html><body>
<p class="oj-doc-ti">REGULATION (EU) 2017/745 OF THE EUROPEAN PARLIAMENT AND OF THE COUNCIL</p><p class="oj-doc-ti">on medical devices</p>
<p class="oj-ti-art">Article 1</p><p class="oj-sti-art">Subject matter and scope</p><p class="oj-normal">{FILL}</p>
<p class="oj-doc-ti">ANNEX I</p><p class="oj-ti-grseq-1">GENERAL SAFETY AND PERFORMANCE REQUIREMENTS</p>
<p class="oj-normal">23.2. The label shall bear the following particulars for reusable surgical devices, sterile packaging, symbols and labelling of instruments. {FILL}</p>
<p class="oj-doc-ti">ANNEX VIII</p><p class="oj-ti-grseq-1">CLASSIFICATION RULES</p>
<p class="oj-ti-grseq-1">CHAPTER I</p><p class="oj-ti-grseq-1">DEFINITIONS SPECIFIC TO CLASSIFICATION RULES</p>
<p class="oj-normal">1. Duration of use. Transient means normally intended for continuous use for less than 60 minutes.</p>
<p class="oj-ti-grseq-1">CHAPTER III</p><p class="oj-ti-grseq-1">CLASSIFICATION RULES</p>
<table><tr><td><p class="oj-normal">4.</p></td><td><p class="oj-ti-grseq-1">NON-INVASIVE DEVICES</p></td></tr></table>
{rule(1, "All non-invasive devices are classified as class I, unless one of the rules set out hereinafter applies.", "4.1.")}
<table><tr><td><p class="oj-normal">5.</p></td><td><p class="oj-ti-grseq-1">INVASIVE DEVICES</p></td></tr></table>
{rule(5, "All invasive devices with respect to body orifices are classified as class I if intended for transient use.", "5.1.")}
{rule(6, "All surgically invasive devices intended for transient use are classified as class IIa unless they are reusable surgical instruments, in which case they are classified as class I.", "5.2.")}
{rule(7, "All surgically invasive devices intended for short-term use are classified as class IIa.", "5.3.")}
<p class="oj-ti-grseq-1">6. ACTIVE DEVICES</p>
{rule(9, "All active therapeutic devices intended to administer or exchange energy are classified as class IIa.", "6.1.")}
{rule(22, "Active therapeutic devices with an integrated or incorporated diagnostic function are classified as class III.", "6.4.")}
<p class="oj-doc-ti">ANNEX IX</p><p class="oj-ti-grseq-1">CONFORMITY ASSESSMENT</p><p class="oj-normal">Chapter I text.</p>
</body></html>'''


def mdr_doc():
    recs, meta = eu.parse_xhtml(mdr_html(), "32017R0745")
    doc = eu.render("32017R0745", "EU MDR", recs, {**meta, "celex": "02017R0745-20250110", "as_of": "2025-01-10", "consolidated": True})
    return recs, meta, doc


# ---------------------------------------------------------------- Annex VIII chunking
def test_annex_viii_rules_are_separate_records_with_context():
    recs, meta, doc = mdr_doc()
    keys = [r["key"] for r in recs]
    assert "eurlex:32017R0745-annex-viii" in keys and "eurlex:32017R0745-annex-i" in keys
    for n in (1, 5, 6, 7, 9, 22):
        assert f"eurlex:32017R0745-annex-viii-rule-{n}" in keys
    assert meta["annex_viii_rules"] == 6
    r6 = next(r for r in recs if r["key"].endswith("annex-viii-rule-6"))["data"]
    assert r6["section"] == "Annex VIII Rule 6" and "reusable surgical instruments" in r6["text"]
    assert "INVASIVE DEVICES" in r6["context"] and "CLASSIFICATION RULES" in r6["context"]
    assert "Rule 7" not in r6["text"] and "short-term" not in r6["text"]          # doosre Rule ka text nahi


def test_annex_viii_render_tags_and_order():
    _, _, doc = mdr_doc()
    assert "[EU Reg 2017/745 Annex VIII Rule 6] All surgically invasive devices intended for transient use" in doc
    pos = [doc.index(x) for x in ("Annex I]", "Annex VIII]", "Annex VIII Rule 1]", "Annex VIII Rule 6]", "Annex VIII Rule 7]",
                                  "Annex VIII Rule 9]", "Annex VIII Rule 22]", "Annex IX]")]
    assert pos == sorted(pos)


def test_annex_viii_not_split_when_rules_absent_and_old_annexes_unchanged():
    html = mdr_html().replace("Rule ", "Regulation ")
    recs, meta = eu.parse_xhtml(html, "32017R0745")
    assert meta["annex_viii_rules"] == 0
    assert any(r["key"].endswith("annex-viii") for r in recs) and not any("rule-" in r["key"] for r in recs)


def test_rule_cross_references_and_rule_lead_form():
    html = mdr_html().replace("are classified as class I if intended for transient use.",
                              "are class I. Rule 7 also applies.")
    recs, _ = eu.parse_xhtml(html, "32017R0745")
    r5 = next(r for r in recs if r["key"].endswith("annex-viii-rule-5"))["data"]
    assert "Rule 7 also applies" in r5["text"]                                      # andar ka zikr naya Rule nahi banata
    lead = mdr_html().replace('<p class="oj-ti-grseq-1">Rule 6</p></td></tr></table>', '<p class="oj-ti-grseq-1">Rule 6 All surgically invasive devices intended for transient use are class IIa.</p></td></tr></table>')
    recs, meta = eu.parse_xhtml(lead, "32017R0745")
    assert any(r["key"].endswith("annex-viii-rule-6") for r in recs)


def test_chunker_keeps_each_rule_in_its_own_chunk():
    _, _, doc = mdr_doc()
    chunks = chunk_text(doc)
    with_r6 = [c for c in chunks if "Annex VIII Rule 6]" in c]
    assert len(with_r6) == 1
    c = with_r6[0]
    assert "Rule 5]" not in c and "Rule 7]" not in c and "reusable surgical instruments" in c


def test_chunker_unchanged_for_plain_text():
    text = "\n\n".join(f"para {i} " + "word " * 40 for i in range(10))
    assert chunk_text(text)[0].startswith("para 0")


# ---------------------------------------------------------------- region lock
def test_regions_fda_mdr_reporting_is_not_eu():
    assert regulatory_regions("FDA MDR report 21 CFR 803") == {"fda"}
    assert regulatory_regions("EU MDR vs FDA classification") == {"fda", "eu"}
    assert regulatory_regions("forceps Class Is Class Ir notified body") == {"eu"}
    assert regulatory_signal("forceps Class Ir kya hai") and regulatory_signal("Annex VIII rules")


def test_strict_scope_never_falls_back_to_other_region():
    names = ["reg__fda", "reg__eu", "reg__uk"]
    assert scope_regulatory(names, "EU MDR forceps", strict=True) == ["reg__eu"]
    assert scope_regulatory(["reg__fda"], "EU MDR", strict=True) == []
    assert scope_regulatory(["reg__fda"], "EU MDR") == ["reg__fda"]                 # purana (non-strict) behaviour
    assert collection_region("reg__us") == "fda"
    assert regsearch.lock_collections(["acme", "reg__fda", "reg__eu"], "CE mark forceps") == ["acme", "reg__eu"]
    assert regsearch.lock_collections(["acme", "reg__fda"], "tell me about forceps") == ["acme", "reg__fda"]
    assert explicit_regions("labelling?", "what about the UK?") == {"uk"}


class FakeRag:
    top_k = 4

    def __init__(self, data):
        self.data, self.calls = data, []

    async def search_many(self, query, collections, top_k=None):
        self.calls.append((query, list(collections), top_k))
        out = [h for c in collections for h in self.data.get(c, [])]
        return sorted(out, key=lambda h: h["score"], reverse=True)[: top_k or self.top_k]


def hit(doc, text, score):
    return {"doc_id": doc, "chunk_index": 0, "text": text, "score": score, "metadata": {}}


def test_eu_question_never_touches_fda_even_when_eu_score_is_low():
    rag = FakeRag({"reg__eu": [hit("eu-32017r0745", "[EU Reg 2017/745 Art 52] conformity", 0.2)],
                   "reg__fda": [hit("ecfr-21", "[21 CFR 878.4800] Class I manual surgical instrument", 0.9),
                                hit("openfda-recall", "[recall:Z-1] forceps recall Class I", 0.8)],
                   "acme": []})
    res = run(regsearch.search_regulatory(rag, ["reg__eu", "reg__fda", "acme"],
                                          "EU MDR forceps Class Is Class Ir Notified Body", "EU MDR forceps Class Is Class Ir Notified Body", 0.4))
    assert res["hits"] == []
    assert all("reg__fda" not in cols for _, cols, _ in rag.calls)


def test_us_question_still_uses_fda_and_ambiguous_question_uses_all():
    rag = FakeRag({"reg__eu": [hit("eu", "[EU Reg 2017/745 Art 52] x", 0.8)],
                   "reg__fda": [hit("ecfr", "[21 CFR 878.4800] manual surgical instrument", 0.9)]})
    res = run(regsearch.search_regulatory(rag, ["reg__eu", "reg__fda"], "FDA class I forceps", "FDA class I forceps", 0.4))
    assert [h["doc_id"] for h in res["hits"]] == ["ecfr"]
    rag = FakeRag({"reg__eu": [hit("eu", "[EU Reg 2017/745 Art 52] manual surgical instrument", 0.8)],
                   "reg__fda": [hit("ecfr", "[21 CFR 878.4800] manual surgical instrument", 0.9)]})
    res = run(regsearch.search_regulatory(rag, ["reg__eu", "reg__fda"], "surgical instrument rules", "surgical instrument rules", 0.4))
    assert {h["doc_id"] for h in res["hits"]} == {"eu", "ecfr"}


def test_classification_rerank_puts_annex_viii_rule_above_annex_i_labelling():
    rag = FakeRag({"reg__eu": [
        hit("eu-32017r0745", "[EU Reg 2017/745 Annex I] 23.4 labelling of reusable surgical devices", 0.61),
        hit("eu-32017r0745", "[EU Reg 2017/745 Annex VIII Rule 6] are reusable surgical instruments, class I", 0.52)]})
    q = "EU MDR Annex VIII reusable surgical instruments classification"
    res = run(regsearch.search_regulatory(rag, ["reg__eu"], q, q, 0.4))
    assert "Annex VIII Rule 6" in res["hits"][0]["text"]
    assert rag.calls[0][2] >= 24 and "Annex VIII" in rag.calls[0][0]                  # bara pool + expanded query
    # labelling ka sawal: Annex I ko neeche nahi dhakelte
    q2 = "EU MDR label requirements for reusable surgical devices"
    assert regsearch.rerank(rag.data["reg__eu"], q2)[0]["text"].startswith("[EU Reg 2017/745 Annex I]")


def test_explicit_rule_number_gets_exact_rule():
    hits = [hit("eu", "[EU Reg 2017/745 Annex VIII Rule 7] short-term", 0.60), hit("eu", "[EU Reg 2017/745 Annex VIII Rule 6] transient", 0.55)]
    assert "Rule 6" in regsearch.rerank(hits, "EU MDR Rule 6 classification")[0]["text"]


# ---------------------------------------------------------------- end-to-end (real chunker + hashing embedder + store)
def test_end_to_end_reusable_surgical_instruments_returns_rule_6():
    _, _, doc = mdr_doc()
    fda = "\n\n".join(f"[21 CFR 878.4800] Manual surgical instrument for general use is Class I exempt. Recall of forceps Class I {FILL}" for _ in range(3))

    async def go():
        with tempfile.TemporaryDirectory() as d:
            emb = HashingEmbedder(512)
            rag = RagService(emb, JsonVectorStore(os.path.join(d, "s.json"), emb.id), None, top_k=4)
            await rag.ingest("eu-32017r0745", doc, "reg__eu")
            await rag.ingest("ecfr-21", fda, "reg__fda")
            q = "EU MDR Annex VIII classification of reusable surgical instruments (forceps)"
            res = await regsearch.search_regulatory(rag, ["reg__eu", "reg__fda"], q, q, 0.0)
            return res
    res = run(go())
    assert res["hits"] and all(h["doc_id"] != "ecfr-21" for h in res["found"])
    assert "Annex VIII Rule 6]" in res["hits"][0]["text"]
