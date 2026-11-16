"""
FDA / eCFR passages ke liye section-level citation ("21 CFR 820.30", "510(k) K123456").
Documents mein har paragraph par [21 CFR 820.30] ya [510k:K123456] tag hota hai (app/services/ecfr.py, openfda.py render),
yahan se usi tag se label nikalta hai. Pure function: koi I/O nahi.
"""
import re

_CFR = re.compile(r"\[(21 CFR (?:\d+\.\d+[\w.()\-]*|Part \d+[^\]]{0,40}))\]")
_FDA = re.compile(r"\[(510k|recall|classification):([\w.\-]{1,40})\]")
_FDA_NAMES = {"510k": "510(k)", "recall": "Recall", "classification": "Classification"}
_MAX = 3


def section_label(doc_id: str, text: str) -> str | None:
    """Chunk ke text se citation label; document regulatory na ho ya tag na mile to None."""
    doc_id = doc_id or ""
    if doc_id.startswith("ecfr-"):
        seen: list[str] = []
        for m in _CFR.finditer(text or ""):
            tag = m.group(1)
            if tag not in seen:
                seen.append(tag)
        if not seen:
            return None
        extra = len(seen) - _MAX
        label = "; ".join(seen[:_MAX])
        return label + (f" (+{extra} more)" if extra > 0 else "")
    if doc_id.startswith("openfda-"):
        seen = []
        for m in _FDA.finditer(text or ""):
            label = f"{_FDA_NAMES[m.group(1)]} {m.group(2)}"
            if label not in seen:
                seen.append(label)
        if not seen:
            return None
        extra = len(seen) - _MAX
        return "; ".join(seen[:_MAX]) + (f" (+{extra} more)" if extra > 0 else "")
    return None
