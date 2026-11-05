"""Page-text OCR fallback through a pinned OpenRouter vision model.

This transcribes complete pages. It is separate from figure-card enrichment,
which describes selected images and cannot recover a scanned document's text.
"""

from __future__ import annotations

import base64
from decimal import Decimal
import json
from time import perf_counter_ns
from typing import Any
from urllib import error, request

from app.ledger import LedgerGuard
from app.settings import settings

OCR_VISION_OPERATION = "ocr_vision"
_PROMPT = (
    "Transcribe the visible text on this document page in reading order. "
    "Preserve headings, lists, equations, labels, and table cells; use Markdown for tables. "
    "Do not summarize, answer questions, invent missing text, or describe decorative images. "
    "Mark unreadable spans as [illegible]. If no text is visible, output exactly [blank page]. "
    "Return only the transcription."
)


class OCRVisionError(RuntimeError):
    pass


def _page_images(raw_bytes: bytes, filename: str, max_pages: int):
    suffix = filename.lower().rsplit(".", 1)[-1]
    if suffix == "pdf":
        try:
            import fitz
            with fitz.open(stream=raw_bytes, filetype="pdf") as document:
                if len(document) > max_pages:
                    raise OCRVisionError(f"{len(document)} pages exceed the {max_pages}-page OCR vision limit")
                for number, page in enumerate(document, start=1):
                    started = perf_counter_ns()
                    scale = min(2.0, 1600 / max(page.rect.width, page.rect.height, 1))
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                    image = pixmap.tobytes("png")
                    yield number, image, "image/png", round((perf_counter_ns()-started)/1_000_000, 3)
        except OCRVisionError:
            raise
        except Exception as exc:
            raise OCRVisionError(f"PDF page rendering failed: {type(exc).__name__}") from exc
    elif suffix in {"png", "jpg", "jpeg"}:
        mime = "image/png" if suffix == "png" else "image/jpeg"
        yield 1, raw_bytes, mime, 0.0
    else:
        raise OCRVisionError(f"page-text vision OCR does not support .{suffix} files")


def _identity(payload: Any, pinned: str):
    if not isinstance(payload, dict):
        raise OCRVisionError("OpenRouter OCR vision response was not JSON")
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    cached = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    model = payload.get("model")
    if model and (model == pinned or pinned.endswith(f"/{model}")):
        model = pinned
    def count(value):
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    cost = usage.get("cost")
    reported_cost = Decimal(str(cost)) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
    return model, {"input_tokens": count(usage.get("prompt_tokens")),
                   "cached_input_tokens": count(cached.get("cached_tokens")),
                   "output_tokens": count(usage.get("completion_tokens")),
                   "cached_output_tokens": None, "requests": 1}, reported_cost


def _post(payload: dict) -> dict:
    key = settings.openrouter_api_key
    if not key:
        raise OCRVisionError("CURRICULUM_TUTOR_OPENROUTER_API_KEY is required for OCR vision fallback")
    req = request.Request(
        f"{settings.openrouter_base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Accept": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=90.0) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        raise OCRVisionError(f"OpenRouter OCR vision returned HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise OCRVisionError(f"OpenRouter OCR vision connection failed: {exc.reason}") from exc


def _response_text(payload: dict) -> str:
    try:
        choice = payload["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OCRVisionError("OpenRouter OCR vision response has no message") from exc
    if choice.get("finish_reason") != "stop":
        raise OCRVisionError(f"OpenRouter OCR vision did not finish the page: {choice.get('finish_reason')!r}")
    if isinstance(content, list):
        content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
    if not isinstance(content, str) or not content.strip():
        raise OCRVisionError("OpenRouter OCR vision returned empty transcription")
    return content.strip()


def transcribe_pages(raw_bytes: bytes, *, filename: str, guard: LedgerGuard | None,
                     telemetry: list[dict] | None = None) -> str:
    """Return page-marked text; fail rather than silently omit a page."""
    if settings.ocr_vision_model != "deepseek/deepseek-v4.1-flash":
        raise OCRVisionError("OCR vision fallback must use deepseek/deepseek-v4.1-flash")
    if not settings.openrouter_api_key:
        raise OCRVisionError("OpenRouter key is missing for OCR vision fallback")
    max_pages = settings.ocr_vision_max_pages
    if max_pages < 1 or settings.ocr_vision_max_output_tokens < 1:
        raise OCRVisionError("OCR vision page/output limits must be positive")
    pages = []
    for number, image, mime, render_ms in _page_images(raw_bytes, filename, max_pages):
        if len(image) > 8_388_608:
            raise OCRVisionError(f"page {number} rendered above the 8 MiB image limit")
        payload = {"model": settings.ocr_vision_model,
                   "messages": [{"role": "user", "content": [
                       {"type": "text", "text": _PROMPT},
                       {"type": "image_url", "image_url": {"url":
                           f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"}},
                   ]}], "max_tokens": settings.ocr_vision_max_output_tokens,
                   "usage": {"include": True}}
        started = perf_counter_ns()
        if guard is None:
            response = _post(payload)
        else:
            response = guard.invoke(
                role=OCR_VISION_OPERATION, provider="openrouter", model=settings.ocr_vision_model,
                operation=OCR_VISION_OPERATION,
                bounds={"max_input_tokens": len(image) + len(_PROMPT.encode("utf-8")) + 4096,
                        "max_output_tokens": settings.ocr_vision_max_output_tokens},
                dispatch=lambda: _post(payload),
                extract=lambda raw: _identity(raw, settings.ocr_vision_model),
            )
        text = _response_text(response)
        if telemetry is not None:
            telemetry.append({"page": number, "render_ms": render_ms, "vision_ms":
                              round((perf_counter_ns()-started)/1_000_000, 3),
                              "image_bytes": len(image), "text_chars": len(text),
                              "model": settings.ocr_vision_model,
                              "blank": text.casefold() == "[blank page]"})
        # The worker's text parser recognizes this exact marker and preserves
        # segment_number as the original PDF page for citation locators.
        pages.append(f"Page {number}\n{text}")
    if not pages:
        raise OCRVisionError("document had no renderable pages")
    return "\n\n".join(pages)
