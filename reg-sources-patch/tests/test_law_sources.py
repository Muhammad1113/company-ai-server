"""UK legislation.gov.uk + EUR-Lex sync (lawsync, uklegislation, eurlex): parsing, diff, approval flow, region scoping.
Network nahi: httpx.MockTransport. Sample XML/XHTML asli sites ke layout ki nakal hai; asli layout ke liye
scripts/check_live_sources.py chalao."""
import asyncio
import tempfile
from urllib.parse import parse_qs

import httpx
import pytest

from app.database.regulatory import RegulatoryRepository
from app.rag.citations import section_label
from app.services import eurlex as eu
from app.services import lawsync
from app.services import uklegislation as uk
from app.services.regulatory import RegulatoryError, RegulatoryService
from app.services.router import collection_region, regulatory_regions, regulatory_signal, scope_regulatory


def run(coro):
    return asyncio.run(coro)


NS = 'xmlns="http://www.legislation.gov.uk/namespaces/legislation" xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dct="http://purl.org/dc/terms/"'
def clml(reg5_text="The manufacturer must register.", extra=""):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Legislation {NS}>
<ukm:Metadata><dc:title>The Medical Devices Regulations 2002</dc:title><dct:modified>2025-06-16</dct:modified></ukm:Metadata>
<Secondary>
 <SecondaryPrelims><Title>The Medical Devices Regulations 2002</Title><Number>2002 No. 618</Number>
   <Preamble><Para><Text>The Secretary of State makes these Regulations in exercise of powers.</Text></Para></Preamble></SecondaryPrelims>
 <Body>
  <Part id="part-1"><Number>PART I</Number><Title>General</Title>
   <P1group><Title>Citation and commencement</Title>
     <P1 id="regulation-1"><Pnumber>1</Pnumber><P1para><Text>These Regulations may be cited as the Medical Devices Regulations 2002.</Text></P1para></P1>
   </P1group>
   <P1group><Title>Registration</Title>
     <P1 id="regulation-5"><Pnumber>5</Pnumber>
       <P1para><Text>{reg5_text}<CommentaryRef Ref="c1"/></Text></P1para>
       <P2><Pnumber PuncBefore="(" PuncAfter=")">2</Pnumber><P2para><Text>Words <Repeal CommentaryRef="c2">old repealed words</Repeal> stay <Addition CommentaryRef="c3">new words</Addition>.</Text></P2para></P2>
     </P1>
   </P1group>
  </Part>
 </Body>
 <Schedules>
  <Schedule id="schedule-1"><Number>SCHEDULE 1</Number><TitleBlock><Title>Essential requirements</Title></TitleBlock>
   <ScheduleBody><P1group><Title>General</Title><P1 id="schedule-1-paragraph-1"><Pnumber>1</Pnumber><P1para><Text>Devices must not compromise safety.</Text></P1para></P1></P1group></ScheduleBody></Schedule>
  <Schedule id="schedule-2"><Number>SCHEDULE 2</Number><TitleBlock><Title>Fees table</Title></TitleBlock>
   <ScheduleBody><Tabular><table><tbody><tr><td>Class I</td><td>10</td></tr><tr><td>Class IIa</td><td>20</td></tr></tbody></table></Tabular></ScheduleBody></Schedule>
  {extra}
 </Schedules>
</Secondary>
<Commentaries><Commentary id="c1"><Para><Text>F1 should-not-appear footnote</Text></Para></Commentary></Commentaries>
</Legislation>'''.encode()


filler = " ".join(["The manufacturer shall ensure conformity with the applicable requirements."]*30)
def xhtml(art10="Manufacturers shall establish a quality management system.", extra_art=""):
    return f'''<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body><div class="eli-container">
<p class="oj-doc-ti">REGULATION (EU) 2017/745 OF THE EUROPEAN PARLIAMENT AND OF THE COUNCIL</p>
<p class="oj-doc-ti">of 5 April 2017</p><p class="oj-doc-ti">on medical devices</p>
<p class="modref"><a href="#M1">&#9660;M1</a></p>
<p class="oj-normal">Whereas:</p>
<table><tr><td><p class="oj-normal">(1)</p></td><td><p class="oj-normal">Directive 90/385/EEC needs replacing. {filler}</p></td></tr>
<tr><td><p class="oj-normal">(2)</p></td><td><p class="oj-normal">A robust framework is needed.&nbsp;<a class="footnote">(1)</a></p></td></tr></table>
<p class="oj-ti-section-1">CHAPTER I</p><p class="oj-ti-section-2">SCOPE AND DEFINITIONS</p>
<p class="oj-ti-art">Article 1</p><p class="oj-sti-art">Subject matter and scope</p>
<p class="oj-normal">1.   This Regulation lays down rules. &#9658;M2 Amended words &#9668;M2</p>
<table><tr><td><p class="oj-normal">(a)</p></td><td><p class="oj-normal">devices for human use;</p></td></tr></table>
<p class="oj-ti-section-1">CHAPTER II</p><p class="oj-ti-section-2">MAKING AVAILABLE ON THE MARKET</p>
<p class="oj-ti-art">Article 10</p><p class="oj-sti-art">General obligations of manufacturers</p>
<p class="oj-normal">1.   {art10}</p><p class="oj-normal">{filler}</p>
<p class="oj-ti-art">Article 9</p><p class="oj-sti-art">(deleted)</p>
{extra_art}
<p class="oj-doc-ti">ANNEX I</p><p class="oj-ti-grseq-1">GENERAL SAFETY AND PERFORMANCE REQUIREMENTS</p>
<p class="oj-normal">1.   Devices shall achieve the performance intended by the manufacturer. {filler}</p>
<p class="oj-doc-ti">ANNEX IX</p><p class="oj-ti-grseq-1">CONFORMITY ASSESSMENT BASED ON A QUALITY MANAGEMENT SYSTEM</p><p class="oj-normal">Chapter I text.</p>
<p class="oj-doc-ti">ANNEX II</p><p class="oj-ti-grseq-1">TECHNICAL DOCUMENTATION</p><p class="oj-normal">The technical documentation shall include.</p>
<p class="oj-note"><a>(1)</a> Council Directive footnote text that must not appear.</p>
</div></body></html>'''



class FakeRag:
    def __init__(self):
        self.docs = {}

    async def ingest(self, doc_id, text, collection, meta):
        self.docs[(collection, doc_id)] = text
        return {"chunks": 1}

    async def delete(self, doc_id, collection):
        self.docs.pop((collection, doc_id), None)


# ---------------------------------------------------------------- UK parser
def test_uk_parse_skips_footnotes_and_repealed_words_and_keeps_structure():
    recs, meta = uk.parse_clml(clml(), "uksi/2002/618")
    assert meta["title"].startswith("The Medical") and meta["modified"] == "2025-06-16"
    txt = " ".join(r["data"]["text"] for r in recs)
    assert "should-not-appear" not in txt and "old repealed words" not in txt and "new words" in txt
    reg5 = next(r for r in recs if r["key"].endswith("regulation-5"))
    assert reg5["data"]["section"] == "reg 5" and reg5["data"]["text"].splitlines()[1].startswith("(2) Words")
    sch1 = next(r for r in recs if r["key"].endswith("schedule-1-paragraph-1"))
    assert sch1["data"]["section"] == "Sch 1 para 1" and "SCHEDULE 1" in sch1["data"]["context"]
    fees = next(r for r in recs if r["key"].endswith("schedule-2"))
    assert "Class I | 10" in fees["data"]["text"]


def test_uk_render_order_tags_and_only_changed_section_rehashes():
    recs, meta = uk.parse_clml(clml(), "uksi/2002/618")
    doc = uk.render("uksi/2002/618", "UK MDR", recs, {**meta, "retrieved": "2026-11-17"})
    assert "[UK SI 2002/618 reg 5]" in doc and "text retrieved 2026-11-17" in doc
    pos = [doc.index(x) for x in ("Preamble", "reg 1]", "reg 5]", "Sch 1 para 1]", "Sch 2]")]
    assert pos == sorted(pos)
    recs2, _ = uk.parse_clml(clml("The manufacturer must register with the MHRA."), "uksi/2002/618")
    h1 = {r["key"]: r["hash"] for r in recs}
    h2 = {r["key"]: r["hash"] for r in recs2}
    assert [k for k in h1 if h1[k] != h2[k]] == ["ukleg:uksi-2002-618-regulation-5"]


def test_uk_guards_and_code_normalising():
    with pytest.raises(lawsync.LawSourceError):
        uk.parse_clml(b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><Legislation/>', "uksi/2002/618")
    with pytest.raises(lawsync.LawSourceError):
        uk.parse_clml(b"<Legislation/>", "uksi/2002/618")
    assert uk.normalize_code("https://www.legislation.gov.uk/uksi/2002/618/contents") == "uksi/2002/618"
    with pytest.raises(lawsync.LawSourceError):
        uk.normalize_code("abc")


def test_long_sections_are_split_not_truncated():
    parts = lawsync.split_text("\n".join(["x" * 20000, "y" * 20000, "z" * 100]))
    assert len(parts) == 2 and all(len(p) <= lawsync.MAX_PART_CHARS for p in parts)
    assert "".join(parts).replace("\n", "") == "x" * 20000 + "y" * 20000 + "z" * 100


# ---------------------------------------------------------------- EU parser + client
def test_eu_parse_articles_annexes_context_and_noise_removal():
    recs, meta = eu.parse_xhtml(xhtml(), "32017R0745")
    assert [r["key"] for r in recs] == [
        "eurlex:32017R0745-preamble", "eurlex:32017R0745-art-1", "eurlex:32017R0745-art-10", "eurlex:32017R0745-art-9",
        "eurlex:32017R0745-annex-i", "eurlex:32017R0745-annex-ix", "eurlex:32017R0745-annex-ii"]
    txt = " ".join(r["data"]["text"] for r in recs)
    assert "footnote text that must not appear" not in txt and "M2" not in txt and "Amended words" in txt
    art1 = recs[1]["data"]
    assert art1["context"] == "CHAPTER I - SCOPE AND DEFINITIONS" and art1["heading"] == "Subject matter and scope"
    assert "(a) devices for human use;" in art1["text"]                       # table row ek line
    assert meta["title"].startswith("REGULATION (EU) 2017/745")


def test_eu_render_roman_annex_order_and_authenticity_note():
    recs, meta = eu.parse_xhtml(xhtml(), "32017R0745")
    doc = eu.render("32017R0745", "EU MDR", recs, {**meta, "celex": "02017R0745-20250110", "as_of": "2025-01-10", "consolidated": True})
    assert "consolidated version of 2025-01-10" in doc and "Official Journal text is authentic" in doc
    pos = [doc.index(x) for x in ("Preamble]", "Art 1]", "Art 9]", "Art 10]", "Annex I]", "Annex II]", "Annex IX]")]
    assert pos == sorted(pos)


def test_eu_error_page_and_unstructured_fallback():
    with pytest.raises(lawsync.LawSourceError):
        eu.parse_xhtml("<html><body><p>Please enable JavaScript</p></body></html>", "32017R0745")
    body = "".join(f"<p>Plain paragraph {i} " + "word " * 40 + "</p>" for i in range(30))
    recs, meta = eu.parse_xhtml("<html><body>" + body + "</body></html>", "32017R0745")
    assert meta["parsed"] is False and recs[0]["key"].endswith("-full")


def test_eu_codes():
    assert eu.normalize_code("https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32017R0745") == "32017R0745"
    assert eu.act_id("02017R0745-20250110") == "32017R0745" and eu.doc_id_for("02017R0745-20250110") == "eu-32017r0745"
    assert eu.item_name("32001L0083") == "EU Dir 2001/83"
    for bad in ("hello", "42017R0745", "32017R745", "32017R0745-2025"):
        with pytest.raises(lawsync.LawSourceError):
            eu.normalize_code(bad)


def test_eu_client_resolves_latest_consolidated_and_never_falls_back_silently():
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path, req.headers.get("accept"), req.headers.get("accept-language")))
        if req.method == "POST":
            assert 'STRSTARTS(STR(?celex), "02017R0745-")' in parse_qs(req.content.decode())["query"][0]
            return httpx.Response(200, json={"results": {"bindings": [{"celex": {"value": "02017R0745-20250110"}}]}})
        assert req.url.path == "/resource/celex/02017R0745-20250110"
        return httpx.Response(200, content=xhtml().encode(), headers={"content-type": "application/xhtml+xml;charset=UTF-8"})

    client = eu.EurLexClient(transport=httpx.MockTransport(handler), delay=0)
    recs, meta = run(client.fetch_act("32017R0745"))
    assert meta["as_of"] == "2025-01-10" and meta["consolidated"] and len(recs) == 7
    assert calls[1][2].startswith("application/xhtml+xml") and calls[1][3] == "eng"
    calls.clear()
    run(client.fetch_act("02017R0745-20250110"))
    assert [c[0] for c in calls] == ["GET"]                                  # khaas version: SPARQL nahi

    def down(req):
        return httpx.Response(503, text="down")
    with pytest.raises(lawsync.LawSourceError, match="by hand"):
        run(eu.EurLexClient(transport=httpx.MockTransport(down), delay=0).fetch_act("32017R0745"))

    def no_consolidated(req):
        if req.method == "POST":
            return httpx.Response(200, json={"results": {"bindings": []}})
        return httpx.Response(200, content=xhtml().encode(), headers={"content-type": "text/html"})
    _, m = run(eu.EurLexClient(transport=httpx.MockTransport(no_consolidated), delay=0).fetch_act("32017R0745"))
    assert m["consolidated"] is False and "originally published" in m["note"]


# ---------------------------------------------------------------- service: approval flow
def _service(state):
    def uk_handler(req):
        return httpx.Response(200, content=state["uk"])

    def eu_handler(req):
        if req.method == "POST":
            return httpx.Response(200, json={"results": {"bindings": [{"celex": {"value": "02017R0745-20250110"}}]}})
        return httpx.Response(200, content=state["eu"].encode(), headers={"content-type": "application/xhtml+xml"})

    repo = RegulatoryRepository(":memory:")
    run(repo.init())
    rag = FakeRag()
    sources = [
        lawsync.LawSource("uk", uk, uk.UKLegislationClient(transport=httpx.MockTransport(uk_handler), delay=0), 3),
        lawsync.LawSource("eu", eu, eu.EurLexClient(transport=httpx.MockTransport(eu_handler), delay=0), 3)]
    return RegulatoryService(repo, rag, tempfile.mkdtemp(), None, law_sources=sources), repo, rag


def test_uk_sync_pending_approve_change_reject_flow():
    state = {"uk": clml(), "eu": xhtml()}
    svc, repo, rag = _service(state)

    async def go():
        st = await svc.law_status("uk")
        assert [i["code"] for i in st["items"]] == ["uksi/2002/618"] and st["collection"] == "reg__uk"
        first = await svc.run_law_sync("uk")
        assert first["changes"] == 5 and first["errors"] == [] and rag.docs == {}          # approval se pehle RAG khali
        assert (await repo.list_pending(source="eu")) == []
        out = await svc.approve(first["pending_id"], "root")
        assert out["documents"] == ["uk-uksi-2002-618"] and "[UK SI 2002/618 reg 5]" in rag.docs[("reg__uk", "uk-uksi-2002-618")]
        with pytest.raises(RegulatoryError):
            await svc.approve(first["pending_id"], "root")                                  # double approve nahi
        assert (await svc.run_law_sync("uk"))["changes"] == 0

        state["uk"] = clml("The manufacturer must register with the MHRA.")
        second = await svc.run_law_sync("uk")
        detail = await repo.get_pending(second["pending_id"])
        assert second["changes"] == 1 and detail["payload"][0]["change"] == "changed"
        await svc.approve(second["pending_id"], "root")
        assert "with the MHRA" in rag.docs[("reg__uk", "uk-uksi-2002-618")]

        state["uk"] = clml("Rejected wording.")
        third = await svc.run_law_sync("uk")
        await svc.reject(third["pending_id"], "root")
        assert (await svc.run_law_sync("uk"))["changes"] == 0                               # reject yaad hai
        assert "Rejected wording" not in rag.docs[("reg__uk", "uk-uksi-2002-618")]
    run(go())


def test_eu_sync_approve_and_removed_article():
    state = {"uk": clml(), "eu": xhtml()}
    svc, repo, rag = _service(state)

    async def go():
        await svc.law_status("eu")
        first = await svc.run_law_sync("eu")
        assert first["versions"] == {"32017R0745": "2025-01-10"} and first["changes"] == 7
        await svc.approve(first["pending_id"], "root")
        doc = rag.docs[("reg__eu", "eu-32017r0745")]
        assert "[EU Reg 2017/745 Art 10]" in doc and "Art 9]" in doc
        state["eu"] = state["eu"].replace('<p class="oj-ti-art">Article 9</p><p class="oj-sti-art">(deleted)</p>', "")
        second = await svc.run_law_sync("eu")
        assert [c["change"] for c in (await repo.get_pending(second["pending_id"]))["payload"]] == ["removed"]
        await svc.approve(second["pending_id"], "root")
        doc = rag.docs[("reg__eu", "eu-32017r0745")]
        assert "Art 9]" not in doc and "Art 10]" in doc
    run(go())


def test_items_validation_limits_and_unknown_source():
    svc, repo, rag = _service({"uk": clml(), "eu": xhtml()})

    async def go():
        await svc.law_status("uk")
        with pytest.raises(RegulatoryError):
            await svc.add_law_item("uk", "nonsense", None)
        assert await svc.add_law_item("uk", "https://www.legislation.gov.uk/ukpga/1987/43/contents", "CPA") == "ukpga/1987/43"
        await svc.add_law_item("uk", "ukpga/2018/12", None)
        with pytest.raises(RegulatoryError, match="at most 3"):
            await svc.add_law_item("uk", "ukpga/2000/1", None)
        assert await svc.remove_law_item("uk", "ukpga/2018/12") and not await svc.remove_law_item("uk", "ukpga/2018/12")
        with pytest.raises(RegulatoryError):
            await svc.law_status("au")
    run(go())


def test_shrunken_source_proposes_nothing_and_errors_are_reported():
    svc, repo, rag = _service({"uk": clml(), "eu": xhtml()})

    async def go():
        many = [{"key": f"ukleg:x-{i}", "kind": uk.KIND, "code": "uksi/2002/618", "data": {"text": str(i)}, "hash": f"h{i}"}
                for i in range(12)]
        await repo.upsert_snapshot(many)

        async def tiny(code):
            return many[:2], {}

        async def boom(code):
            raise lawsync.LawSourceError("legislation.gov.uk returned HTTP 503")
        changes, errors, _ = await lawsync.build_changes(tiny, uk.KIND, repo, ["uksi/2002/618"])
        assert changes == [] and "far fewer" in errors[0]["error"]
        changes, errors, _ = await lawsync.build_changes(boom, uk.KIND, repo, ["uksi/2002/618"])
        assert changes == [] and errors[0]["error"].endswith("503")
    run(go())


# ---------------------------------------------------------------- router + citations
def test_region_detection_and_collection_scoping():
    assert regulatory_regions("What does EU MDR say about labelling?") == {"eu"}
    assert regulatory_regions("UK MDR 2002 registration with MHRA") == {"uk"}
    assert regulatory_regions("Do I need UKCA or CE mark for the UK?") == {"uk", "eu"}
    assert regulatory_regions("FDA 510(k) for forceps in USA") == {"fda"}
    assert regulatory_regions("Is TGA approval needed in Australia") == {"au"}
    assert regulatory_regions("tell us about forceps sizes") == set()
    assert regulatory_signal("UKCA rules") and regulatory_signal("EUDAMED registration")
    names = ["reg__fda", "reg__eu", "reg__uk", "reg__eu-mdr", "reg__au"]
    assert scope_regulatory(names, "EU MDR labelling") == ["reg__eu", "reg__eu-mdr"]
    assert scope_regulatory(names, "labelling rules", "what about the UK?") == ["reg__uk"]
    assert scope_regulatory(names, "labelling rules") == names
    assert scope_regulatory(["reg__fda"], "EU MDR") == ["reg__fda"]
    assert collection_region("reg__USA") == "fda"


def test_uk_eu_citation_labels():
    assert section_label("uk-uksi-2002-618", "[UK SI 2002/618 reg 5] a [UK SI 2002/618 Sch 1 para 2] b") == \
        "UK SI 2002/618 reg 5; UK SI 2002/618 Sch 1 para 2"
    assert section_label("eu-32017r0745", "[EU Reg 2017/745 Art 10] x") == "EU Reg 2017/745 Art 10"
    assert section_label("other-doc", "[UK x]") is None
