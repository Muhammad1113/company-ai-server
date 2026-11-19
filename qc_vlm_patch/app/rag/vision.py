"""
PDF pages -> vision model (Qwen3.5 + mmproj, llama-server) -> text.

Sirf INGEST (upload) ke waqt chalta hai, sawal poochte waqt nahi. Isliye chat ki speed par asar nahi.
Jo pages mein text kam hai (scanned) ya bari tasveer / table / drawing hai, unka text VLM padhta hai
aur us page ke text ke saath jod diya jata hai. Phir normal chunking + embedding + page number.

Zaroorat: pip install pypdfium2 pillow   (llama-server --mmproj ... ke saath chalna chahiye)
"""
import asyncio
import base64
import io
import logging
import re

import httpx

from app.core.config import settings

log = logging.getLogger("vision")

_PROMPT = (
    "This is one page from a surgical-instruments document (catalog, specification sheet, certificate, "
    "technical drawing or manual). Convert it to plain text that a search system can index.\n"
    "Rules:\n"
    "1. Transcribe all readable text, numbers, sizes, codes and standards EXACTLY as printed.\n"
    "2. For tables, write each row on one line as 'Header: value | Header: value', keeping the header names.\n"
    "3. For pictures, drawings or diagrams, add one short factual line starting with 'Figure:' that says what is "
    "shown, followed by any labels or dimensions printed on it.\n"
    "4. Never guess or invent. If something is unreadable write [unreadable].\n"
    "5. No commentary and no markdown headings.\n"
    "If the page has no useful content, reply exactly: NO_CONTENT"
)

_THINK = re.compile(r"<think>.*?</think>", re.S)


class VisionError(Exception):
    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


def _base_url() -> str:
    return (settings.VLM_BASE_URL or settings.LLAMA_BASE_URL).rstrip("/")


def _model() -> str:
    return settings.VLM_MODEL or settings.LLAMA_MODEL


# ---------------------------------------------------------------- page selection + rendering (sync, thread mein)

def _select_and_render(data: bytes, pages: list[str]) -> tuple[dict[int, bytes], int]:
    """Returns ({page_index: jpeg_bytes}, skipped_count)."""
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as pdfium_c
    except ImportError as exc:
        raise VisionError("Vision support needs: pip install pypdfium2 pillow", 503) from exc

    mode = settings.VLM_MODE
    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as exc:
        raise VisionError("Could not open the PDF for the vision model", 422) from exc

    targets: list[int] = []
    try:
        count = min(len(pdf), len(pages))
        for i in range(count):
            thin = len(pages[i].strip()) < settings.VLM_MIN_TEXT_CHARS
            if mode == "all":
                want = True
            elif mode == "scanned":
                want = thin
            else:                                   # figures
                want = thin or _has_figure(pdf[i], pdfium_c)
            if want:
                targets.append(i)

        skipped = max(0, len(targets) - settings.VLM_MAX_PAGES)
        targets = targets[: settings.VLM_MAX_PAGES]

        rendered: dict[int, bytes] = {}
        for i in targets:
            image = pdf[i].render(scale=2).to_pil().convert("RGB")
            longest = max(image.size)
            if longest > settings.VLM_MAX_SIDE:
                ratio = settings.VLM_MAX_SIDE / longest
                image = image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))))
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=88)
            rendered[i] = buf.getvalue()
        return rendered, skipped
    finally:
        pdf.close()


def _has_figure(page, pdfium_c) -> bool:
    """Bari image (page ka >=5%) ya bahut saare vector paths (drawing/table). Chhote logo ignore."""
    width, height = page.get_size()
    page_area = max(width * height, 1.0)
    paths = 0
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE, pdfium_c.FPDF_PAGEOBJ_PATH]):
        if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
            left, bottom, right, top = obj.get_bounds()
            if (right - left) * (top - bottom) >= 0.05 * page_area:
                return True
        else:
            paths += 1
            if paths >= settings.VLM_MIN_PATHS:
                return True
    return False


# ---------------------------------------------------------------- VLM call

async def _describe(client: httpx.AsyncClient, jpeg: bytes) -> str:
    b64 = base64.b64encode(jpeg).decode()
    payload = {
        "model": _model(),
        "temperature": 0.1,
        "max_tokens": settings.VLM_MAX_TOKENS,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": _PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
    }
    try:
        response = await client.post(f"{_base_url()}/v1/chat/completions", json=payload)
    except httpx.HTTPError as exc:
        raise VisionError(f"Vision server not reachable at {_base_url()}: {exc.__class__.__name__}") from exc

    if response.status_code >= 400:
        hint = " (start llama-server with --mmproj <mmproj-file>.gguf)" if "image" in response.text.lower() else ""
        raise VisionError(f"Vision server error {response.status_code}{hint}: {response.text[:200]}")

    content = response.json()["choices"][0]["message"].get("content") or ""
    return _THINK.sub("", content).strip()


# ---------------------------------------------------------------- public API

async def enrich_pdf(data: bytes, text: str) -> tuple[str, dict]:
    """text = extract_text ka output (pages \\f se juday hue). Returns (naya text, stats)."""
    pages = text.split("\f")
    rendered, skipped = await asyncio.to_thread(_select_and_render, data, pages)

    done = failed = 0
    timeout = httpx.Timeout(settings.VLM_TIMEOUT, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for n, (index, jpeg) in enumerate(rendered.items()):
            try:
                described = await _describe(client, jpeg)
            except VisionError:
                if n == 0:
                    raise                       # server/mmproj ka masla: chup-chaap khali ingest na ho
                failed += 1
                log.warning("vision failed on page %d", index + 1)
                continue
            if not described or described.strip().upper().startswith("NO_CONTENT"):
                continue
            existing = pages[index].strip()
            pages[index] = (existing + "\n\n" if existing else "") + "Page image text:\n" + described
            done += 1

    new_text = "\f".join(pages)
    if rendered and done == 0 and not new_text.replace("\f", "").strip():
        raise VisionError("The vision model could not read any page of this PDF", 422)

    return new_text, {"vlm_pages": done, "vlm_failed": failed, "vlm_skipped": skipped}
