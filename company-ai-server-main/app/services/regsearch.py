"""Regulatory (reg__*) retrieval: strict region lock + classification-rule aware ranking.

Do masle jo EU MDR chat testing mein samne aaye:

1. EU / CE / MDR ka sawal hone par US FDA (reg__fda / reg__us) ka Class 1 aur recall data mix ho jata tha. Wajah: jab region ki
   collection se score-threshold ke upar kuch na milta to "sab collections" par fallback hota tha (FDA samet). Ab jab sawal mein
   region saaf ho, doosre regions ki reg__* collections KABHI search nahi hotin; fallback sirf company ki apni collections tak.
2. "Reusable surgical instruments ki classification" par Annex I ke labelling chunks Annex VIII Rule 6 se upar aa jate the.
   Ab (a) eurlex.py har Rule alag record banata hai, (b) chunker Rule ke beech chunk todta hai, (c) yahan classification ke sawal par
   bara candidate pool uthake Annex VIII ko upar aur Annex I (GSPR/labelling) ko neeche kiya jata hai.

Pure functions + ek async orchestrator. GPU / LLM call nahi.
"""
from __future__ import annotations

import re
from typing import Any

from app.services.router import collection_region, explicit_regions, scope_regulatory

A8_BOOST = 0.12              # Annex VIII ka chunk classification ke sawal ka asal jawab hai
RULE_BOOST = 0.20            # sawal mein "Rule 6" likha ho aur chunk usi Rule ka ho
A1_PENALTY = 0.08            # Annex I (GSPR / labelling) classification ke sawal ko gumrah karta tha
POOL_FACTOR = 6              # classification sawal par top_k ka itna guna candidates uthao, phir rerank
MIN_POOL = 24

_CLASS_LEVEL = r"(?:i[smr]?|ii[ab]?|iii)"
_CLASSIFICATION = (
    re.compile(r"\bclassif\w*", re.I),
    re.compile(r"\bclass\s+" + _CLASS_LEVEL + r"\b", re.I),
    re.compile(r"\bclass\s+[123]\b", re.I),
    re.compile(r"\brules?\s+\d{1,2}\b", re.I),
    re.compile(r"\bannex\s+viii\b", re.I),
)
_LABELLING = re.compile(r"\b(?:label\w*|ifu|instructions? for use|gspr|general safety and performance|annex\s+i)\b", re.I)
_REUSABLE_SURGICAL = re.compile(r"\breusable\b.{0,30}\bsurgical\b|\bsurgical\b.{0,30}\breusable\b|\bclass\s+ir\b", re.I)
_RULE_NUM = re.compile(r"\brules?\s+(\d{1,2})\b", re.I)
_A8_TAG = re.compile(r"\[EU [^\]\n]*Annex VIII(?: Rule (\d{1,2}))?\]")
_A1_TAG = re.compile(r"\[EU [^\]\n]*Annex I\]")


def classification_intent(text: str) -> bool:
    """True: sawal device classification (Class I/Is/Im/Ir/IIa/IIb/III, Rule N, Annex VIII) ke baare mein hai."""
    t = text or ""
    return any(p.search(t) for p in _CLASSIFICATION)


def expand_query(text: str, regions: set[str]) -> str:
    """EU classification ke sawal par retrieval query mein Annex VIII ki zabaan jodo (sirf search ke liye, user ko nahi dikhta)."""
    if "eu" not in regions or not classification_intent(text):
        return text
    extra = " (EU MDR Annex VIII Chapter III classification rules)"
    if _REUSABLE_SURGICAL.search(text):
        extra += " Rule 6 surgically invasive devices intended for transient use reusable surgical instruments class Ir Article 52"
    return text + extra


def rerank(hits: list[dict], query: str) -> list[dict]:
    """Classification ke sawal par EU Annex VIII chunks upar, Annex I neeche. Doosre sawalon par hits jaise hain waise."""
    if not classification_intent(query):
        return hits
    wanted = {int(n) for n in _RULE_NUM.findall(query)}
    labelling = bool(_LABELLING.search(query))
    out: list[dict] = []
    for h in hits:
        text = h.get("text") or ""
        adj = 0.0
        a8 = list(_A8_TAG.finditer(text))
        if a8:
            adj += A8_BOOST
            if wanted & {int(m.group(1)) for m in a8 if m.group(1)}:
                adj += RULE_BOOST
        elif _A1_TAG.search(text) and not labelling:
            adj -= A1_PENALTY
        out.append({**h, "score": h["score"] + adj, "raw_score": h["score"]} if adj else h)
    out.sort(key=lambda h: h["score"], reverse=True)
    return out


def lock_collections(names: list[str], *texts: str) -> list[str]:
    """Sawal mein region saaf ho to doosre regions ki reg__* collections hata do (company ki apni collections rehti hain).
    Region na mile to names jaisi hain waisi."""
    regions = explicit_regions(*texts)
    if not regions:
        return names
    return [n for n in names if not n.startswith("reg__") or collection_region(n) in regions]


async def search_regulatory(rag: Any, names: list[str], question: str, retrieval_query: str, min_score: float) -> dict:
    """Auto mode ka regulatory retrieval. names = user ko dikhne wali saari collections.

    Returns {"found", "hits", "scoped", "regions"}.
      scoped = jin reg__* collections mein pehle search hua (region ke hisaab se, strict).
    Region saaf ho to fallback bhi region lock ke andar rehta hai (kabhi doosre region ki reg__* nahi).
    """
    regions = explicit_regions(question, retrieval_query)
    reg_names = [n for n in names if n.startswith("reg__")]
    scoped = scope_regulatory(reg_names, question, retrieval_query, strict=True)
    query = expand_query(retrieval_query, regions)
    pool = max(rag.top_k * POOL_FACTOR, MIN_POOL) if classification_intent(retrieval_query) else None

    found: list[dict] = []
    hits: list[dict] = []
    if scoped:
        found = await rag.search_many(query, scoped, top_k=pool)
        found = rerank(found, retrieval_query)[: max(rag.top_k, 1)]
        hits = [h for h in found if h["score"] >= min_score]
    if not hits:
        allowed = lock_collections(names, question, retrieval_query)
        rest = [n for n in allowed if n not in scoped]
        if rest:
            more = await rag.search_many(query, rest)
            found = sorted(found + more, key=lambda h: h["score"], reverse=True)[: max(rag.top_k, 1)]
            hits = [h for h in found if h["score"] >= min_score]
    return {"found": found, "hits": hits, "scoped": scoped, "regions": regions}


__all__ = ["classification_intent", "expand_query", "rerank", "lock_collections", "search_regulatory"]
