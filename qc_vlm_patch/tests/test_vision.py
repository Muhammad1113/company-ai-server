import asyncio
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.core.config import settings
from app.rag import vision
from app.rag.chunker import chunk_pages
from app.rag.extractors import ExtractionError, extract_text


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        out = {"choices": [{"message": {"content": "<think>x</think>Figure: needle holder. Length: 180 mm"}}]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture()
def fake_vlm(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(settings, "VLM_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    yield
    server.shutdown()


def _pdf(kind: str) -> bytes:
    img = ImageReader(Image.new("RGB", (800, 600), (200, 200, 220)))
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    if kind == "mixed":      # p1 text, p2 sirf image, p3 text + bari image
        c.drawString(72, 750, "Page one has plenty of normal text. " * 12); c.showPage()
        c.drawImage(img, 40, 100, 500, 600); c.showPage()
        c.drawString(72, 750, "Page three also has plenty of normal text here. " * 10)
        c.drawImage(img, 40, 100, 500, 500); c.showPage()
    else:                    # poora scanned
        for _ in range(2):
            c.drawImage(img, 40, 100, 500, 600); c.showPage()
    c.save()
    return buf.getvalue()


def test_page_numbers_survive_extraction():
    text, _ = extract_text("a.pdf", _pdf("mixed"))
    assert {p for p, _ in chunk_pages(text)} == {1, 3}      # page 2 mein text nahi


def test_scanned_pdf_rejected_unless_allowed():
    with pytest.raises(ExtractionError) as err:
        extract_text("s.pdf", _pdf("scan"))
    assert err.value.status == 422
    text, meta = extract_text("s.pdf", _pdf("scan"), allow_scanned=True)
    assert meta["scanned"] == "1" and text.replace("\f", "") == ""


def test_scanned_mode_reads_only_thin_pages(fake_vlm, monkeypatch):
    monkeypatch.setattr(settings, "VLM_MODE", "scanned")
    text, _ = extract_text("a.pdf", _pdf("mixed"))
    new, stats = asyncio.run(vision.enrich_pdf(_pdf("mixed"), text))
    assert stats["vlm_pages"] == 1
    assert "needle holder" in new.split("\f")[1] and "<think>" not in new


def test_figures_mode_also_reads_pages_with_big_images(fake_vlm, monkeypatch):
    monkeypatch.setattr(settings, "VLM_MODE", "figures")
    text, _ = extract_text("a.pdf", _pdf("mixed"))
    new, stats = asyncio.run(vision.enrich_pdf(_pdf("mixed"), text))
    pages = new.split("\f")
    assert stats["vlm_pages"] == 2 and "needle holder" in pages[2] and "needle holder" not in pages[0]


def test_fully_scanned_pdf_becomes_searchable(fake_vlm, monkeypatch):
    monkeypatch.setattr(settings, "VLM_MODE", "scanned")
    data = _pdf("scan")
    text, _ = extract_text("s.pdf", data, allow_scanned=True)
    new, stats = asyncio.run(vision.enrich_pdf(data, text))
    assert stats["vlm_pages"] == 2 and [p for p, _ in chunk_pages(new)] == [1, 2]


def test_server_down_gives_clear_error(monkeypatch):
    monkeypatch.setattr(settings, "VLM_MODE", "scanned")
    monkeypatch.setattr(settings, "VLM_BASE_URL", "http://127.0.0.1:1")
    data = _pdf("scan")
    text, _ = extract_text("s.pdf", data, allow_scanned=True)
    with pytest.raises(vision.VisionError):
        asyncio.run(vision.enrich_pdf(data, text))


def test_max_pages_cap(fake_vlm, monkeypatch):
    monkeypatch.setattr(settings, "VLM_MODE", "all")
    monkeypatch.setattr(settings, "VLM_MAX_PAGES", 1)
    text, _ = extract_text("a.pdf", _pdf("mixed"))
    _, stats = asyncio.run(vision.enrich_pdf(_pdf("mixed"), text))
    assert stats["vlm_pages"] == 1 and stats["vlm_skipped"] == 2
