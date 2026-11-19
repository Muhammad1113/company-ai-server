import re

# Regulatory render har paragraph par [tag] lagata hai. Annex VIII ka har Rule ("... Annex VIII Rule 6") apna alag chunk
# hona chahiye: warna Rule 5 ka aakhri hissa + Rule 6 + Rule 7 ek chunk mein mil kar ranking kharab karte hain.
_TAG = re.compile(r"^\[([^\]\n]{1,80})\]")
_RULE_TAG = re.compile(r"\bRule \d{1,2}$")


def _tag(unit: str) -> str | None:
    m = _TAG.match(unit)
    return m.group(1) if m else None


def _hard_break(prev: str | None, tag: str | None) -> bool:
    """True: tag badla aur dono mein se ek Rule-tag hai (tag-less tukde = pichle ka hissa, break nahi)."""
    if tag is None or prev is None or tag == prev:
        return False
    return bool(_RULE_TAG.search(tag) or _RULE_TAG.search(prev))



def chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    """
    Paragraph-aware chunking. Har chunk <= size + overlap + 2 chars.
    """
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("need size > 0 and 0 <= overlap < size")

    text = re.sub(r"[ \t]+", " ", text or "").strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    units: list[str] = []
    for para in paragraphs:
        while len(para) > size:
            cut = para.rfind(" ", 0, size)
            if cut < size // 2:
                cut = size
            units.append(para[:cut].strip())
            para = para[cut:].strip()
        if para:
            units.append(para)

    chunks: list[str] = []
    current = ""
    last_tag: str | None = None

    for unit in units:
        tag = _tag(unit)
        if current and _hard_break(last_tag, tag):
            chunks.append(current)                      # naya Rule: pichle chunk ki overlap saath nahi
            current = unit
        elif not current:
            current = unit
        elif len(current) + 2 + len(unit) <= size:
            current += "\n\n" + unit
        else:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            if tail and " " in tail:
                tail = tail[tail.index(" ") + 1:]
            current = f"{tail}\n\n{unit}" if tail else unit
        last_tag = tag or last_tag

    if current:
        chunks.append(current)

    return chunks


def chunk_pages(text: str, size: int = 800, overlap: int = 100) -> list[tuple[int | None, str]]:
    """[(page, chunk)]. Text mein \\f (PDF page break) ho to har page alag chunk hota hai aur page number milta hai;
    warna page None (DOCX/TXT)."""
    if "\f" not in (text or ""):
        return [(None, c) for c in chunk_text(text, size, overlap)]
    out: list[tuple[int | None, str]] = []
    for number, page_text in enumerate(text.split("\f"), start=1):
        out.extend((number, c) for c in chunk_text(page_text, size, overlap))
    return out
