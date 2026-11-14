import re


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

    for unit in units:
        if not current:
            current = unit
        elif len(current) + 2 + len(unit) <= size:
            current += "\n\n" + unit
        else:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            if tail and " " in tail:
                tail = tail[tail.index(" ") + 1:]
            current = f"{tail}\n\n{unit}" if tail else unit

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
