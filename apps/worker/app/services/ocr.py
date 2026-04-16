from __future__ import annotations

import json
from urllib import error, request

from app.settings import settings


class OCRProviderError(RuntimeError):
    pass


def extracted_text_density(text: str, raw_bytes: bytes) -> float:
    if not raw_bytes:
        return 0.0
    return min(len(text.strip()) / max(len(raw_bytes), 1), 1.0)


def should_run_ocr(text: str, raw_bytes: bytes, *, threshold: float) -> bool:
    del raw_bytes, threshold
    return not text.strip()


def run_ocr(raw_bytes: bytes, *, filename: str, mime_type: str) -> str:
    if not settings.unstructured_api_key:
        raise OCRProviderError("CURRICULUM_TUTOR_UNSTRUCTURED_API_KEY is required when OCR fallback is triggered")
    boundary = "eduground-ocr-boundary"
    body = _multipart_body(boundary, raw_bytes, filename=filename, mime_type=mime_type)
    req = request.Request(
        settings.unstructured_api_url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {settings.unstructured_api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with request.urlopen(req, timeout=60.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:  # pragma: no cover - network/provider failure
        detail = exc.read().decode("utf-8", errors="replace")
        raise OCRProviderError(f"Unstructured OCR request failed with status {exc.code}: {detail}") from exc
    except error.URLError as exc:  # pragma: no cover - network/provider failure
        raise OCRProviderError(f"Unstructured OCR request failed: {exc.reason}") from exc
    if not isinstance(payload, list):
        raise OCRProviderError("Unstructured OCR response was malformed")
    text = "\n\n".join((item.get("text") or "").strip() for item in payload if isinstance(item, dict)).strip()
    if not text:
        raise OCRProviderError("Unstructured OCR returned no text")
    return text


def _multipart_body(boundary: str, raw_bytes: bytes, *, filename: str, mime_type: str) -> bytes:
    parts = [
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="strategy"\r\n\r\n'
        f"{settings.unstructured_ocr_strategy}\r\n",
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
        f"Content-Type: {mime_type or 'application/octet-stream'}\r\n\r\n",
    ]
    body = "".join(parts).replace("{filename}", filename).encode("utf-8") + raw_bytes
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body
