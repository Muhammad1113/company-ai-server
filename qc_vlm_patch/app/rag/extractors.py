"""
File -> plain text (.txt .md .pdf .docx). Pure module (settings import nahi karta).

Scanned PDF (sirf images) ka text nahi nikalta: OCR alag cheez hai. Aisi file par
saaf 422 error milta hai, chup-chaap khali document ingest nahi hota.
"""
import io
import re
from pathlib import PurePath

SUPPORTED = (".txt", ".md", ".pdf", ".docx")


class ExtractionError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _clean_page(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clean(text: str) -> str:
    """Har page alag clean hota hai; \f (page break) bacha rehta hai taake page number sahi rahe.
    (Pehle \f ko \n\n bana diya jata tha => PDF ke saare pages ek chunk mein mil jate aur page number None ho jata.)"""
    if "\f" in text:
        return "\f".join(_clean_page(part) for part in text.split("\f"))
    return _clean_page(text)


def _text_file(data: bytes) -> tuple[str, dict]:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), {"encoding": "utf-16"}
    try:
        return data.decode("utf-8-sig"), {"encoding": "utf-8"}
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), {"encoding": "cp1252"}


def _pdf(data: bytes, max_pages: int, allow_scanned: bool = False) -> tuple[str, dict]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ExtractionError("PDF support needs: pip install pypdf", 503) from exc

    try:
        reader = PdfReader(io.BytesIO(data))

        if reader.is_encrypted and not reader.decrypt(""):
            raise ExtractionError("PDF is password protected", 422)

        pages = len(reader.pages)
        if pages > max_pages:
            raise ExtractionError(f"PDF has {pages} pages; the limit is {max_pages}", 413)

        parts = [(page.extract_text() or "") for page in reader.pages]

    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError("Could not read the PDF (corrupt or unsupported)", 422) from exc

    # \f = page break: ingest isse har chunk ka page number nikalta hai (chunk kabhi 2 pages mein nahi phailta)
    text = "\f".join(parts)
    empty_pages = sum(1 for p in parts if len(p.strip()) < 10)

    scanned = sum(len(p.strip()) for p in parts) < 10 * pages
    if scanned and not allow_scanned:
        raise ExtractionError(
            "This PDF has little or no extractable text (scanned images?). "
            "OCR is required for scanned PDFs.", 422,
        )

    meta = {"pages": pages, "empty_pages": empty_pages}
    if scanned:
        meta["scanned"] = 1          # allow_scanned=True: text khali hai, vision model (app/rag/vision.py) padhega
    return text, meta


def _docx(data: bytes) -> tuple[str, dict]:
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise ExtractionError("DOCX support needs: pip install python-docx", 503) from exc

    try:
        doc = Document(io.BytesIO(data))
        parts = []
        for child in doc.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                parts.append(Paragraph(child, doc).text)
            elif tag == "tbl":
                rows = [
                    " | ".join(cell.text.strip() for cell in row.cells)
                    for row in Table(child, doc).rows
                ]
                parts.append("\n".join(rows))
    except Exception as exc:
        raise ExtractionError("Could not read the DOCX file (corrupt or not a .docx)", 422) from exc

    return "\n\n".join(parts), {}


def extract_text(
    filename: str,
    data: bytes,
    *,
    max_pages: int = 500,
    max_chars: int = 500_000,
    allow_scanned: bool = False,
) -> tuple[str, dict]:
    """Returns (text, metadata: dict[str, str]). Raises ExtractionError(status)."""

    name = PurePath(filename).name
    ext = PurePath(name).suffix.lower()

    if ext not in SUPPORTED:
        hint = " (save old .doc files as .docx)" if ext == ".doc" else ""
        raise ExtractionError(
            f"Unsupported file type '{ext or name}'. Supported: {', '.join(SUPPORTED)}{hint}",
            415,
        )

    if not data:
        raise ExtractionError("File is empty", 422)

    if ext in (".txt", ".md"):
        text, meta = _text_file(data)
    elif ext == ".pdf":
        text, meta = _pdf(data, max_pages, allow_scanned)
    else:
        text, meta = _docx(data)

    if ext != ".pdf":
        text = text.replace("\f", "\n\n")      # txt/docx mein form-feed page break nahi hai
    text = _clean(text)

    if not text.replace("\f", "").strip() and not meta.get("scanned"):
        raise ExtractionError("No text found in the file", 422)

    if len(text) > max_chars:
        raise ExtractionError(
            f"Document has {len(text)} characters; the limit is {max_chars}. "
            "Split it into smaller files.", 413,
        )

    meta.update({"filename": name[:255], "type": ext[1:], "chars": len(text)})
    return text, {k: str(v) for k, v in meta.items()}
