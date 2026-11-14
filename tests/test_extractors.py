import io

from app.rag.extractors import ExtractionError, extract_text

SENTENCE = "Customers can request a refund within thirty days of purchase. "


def make_pdf(page_texts):
    """Dependency-free minimal PDF (har page par ek text line; "" => khali page)."""
    esc = lambda t: t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    n = len(page_texts)
    page_ids = [4 + 2 * i for i in range(n)]
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {n} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(page_texts):
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {5 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 3 0 R >> >> >>".encode()
        )
        stream = f"BT /F1 12 Tf 72 720 Td ({esc(text)}) Tj ET".encode() if text else b""
        objs.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")

    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def make_docx():
    from docx import Document
    doc = Document()
    doc.add_paragraph("Leave policy")
    doc.add_paragraph("Employees receive 20 days of annual leave.")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Type", "Days"
    table.cell(1, 0).text, table.cell(1, 1).text = "Sick", "12"
    doc.add_paragraph("Contact HR for questions.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _err(fn):
    try:
        fn()
    except ExtractionError as exc:
        return exc
    raise AssertionError("expected ExtractionError")


def test_text_pdf_extracts_with_metadata():
    data = make_pdf([SENTENCE * 3, "Refunds go to the original payment method. " * 3])
    text, meta = extract_text("Policy.PDF", data)

    assert "refund within thirty days" in text and "original payment method" in text
    assert meta["type"] == "pdf" and meta["pages"] == "2" and meta["filename"] == "Policy.PDF"
    assert meta["empty_pages"] == "0" and all(isinstance(v, str) for v in meta.values())


def test_scanned_like_pdf_is_rejected_clearly():
    err = _err(lambda: extract_text("scan.pdf", make_pdf(["", "", ""])))
    assert err.status == 422 and "OCR" in str(err)


def test_pdf_limits_and_corruption():
    two_pages = make_pdf([SENTENCE * 3, SENTENCE * 3])
    assert _err(lambda: extract_text("a.pdf", two_pages, max_pages=1)).status == 413
    assert _err(lambda: extract_text("a.pdf", b"%PDF-1.4 not really a pdf")).status == 422
    assert _err(lambda: extract_text("a.pdf", two_pages, max_chars=50)).status == 413


def test_docx_paragraphs_and_tables_in_order():
    text, meta = extract_text("leave.docx", make_docx())
    assert text.index("Leave policy") < text.index("Type | Days") < text.index("Contact HR")
    assert "Sick | 12" in text and meta["type"] == "docx"


def test_docx_corrupt_and_not_a_zip():
    assert _err(lambda: extract_text("x.docx", b"this is not a zip")).status == 422


def test_text_encodings():
    assert extract_text("a.txt", "hello wörld".encode("utf-8"))[0] == "hello wörld"
    assert extract_text("a.md", b"\xef\xbb\xbfwith bom")[0] == "with bom"
    assert extract_text("a.txt", "utf16 text".encode("utf-16"))[0] == "utf16 text"
    text, meta = extract_text("a.txt", "caf\xe9".encode("cp1252"))
    assert text == "café" and meta["encoding"] == "cp1252"
    urdu = "چھٹیوں کی پالیسی: ہر ملازم کو 20 دن کی چھٹی ملتی ہے۔"
    assert extract_text("u.txt", urdu.encode("utf-8"))[0] == urdu


def test_unsupported_empty_and_blank():
    assert _err(lambda: extract_text("a.xlsx", b"x")).status == 415
    err = _err(lambda: extract_text("old.doc", b"x"))
    assert err.status == 415 and ".docx" in str(err)
    assert _err(lambda: extract_text("noext", b"x")).status == 415
    assert _err(lambda: extract_text("a.txt", b"")).status == 422
    assert _err(lambda: extract_text("a.txt", b"   \n\n  ")).status == 422


def test_path_traversal_filename_is_reduced_to_basename():
    _, meta = extract_text("..\\..\\secret\\notes.txt", b"hello")
    assert meta["filename"] in ("notes.txt", "..\\..\\secret\\notes.txt")
    assert "/" not in meta["filename"]


def test_clean_normalizes_whitespace_and_nulls():
    text, _ = extract_text("a.txt", b"line1\r\n\r\n\r\n\r\nline2\x00  \n")
    assert text == "line1\n\nline2"
