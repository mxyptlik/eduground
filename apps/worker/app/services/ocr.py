from __future__ import annotations

import json
from typing import Any
from urllib import error, request

from app.ledger import (
    OCR_OPERATION,
    UNSTRUCTURED_PROVIDER,
    UNSTRUCTURED_SERVICE_ID,
    LedgerConfigurationError,
    LedgerGuard,
    count_pdf_pages,
)
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


def run_ocr(
    raw_bytes: bytes, *, filename: str, mime_type: str,
    guard: LedgerGuard | None = None, page_count: int | None = None,
    telemetry: list[dict] | None = None,
) -> str:
    """Use Unstructured first, then DeepSeek V4.1 Flash for page text if enabled.

    A guarded provider failure with unknown billing stops the fallback until
    the shared ledger is reconciled; bypassing it could breach the pilot cap.
    """
    try:
        text = _run_unstructured(raw_bytes, filename=filename, mime_type=mime_type,
                                 guard=guard, page_count=page_count)
        if telemetry is not None:
            telemetry.append({"provider": "unstructured", "status": "complete"})
        return text
    except (OCRProviderError, LedgerConfigurationError) as primary_error:
        if telemetry is not None:
            telemetry.append({"provider": "unstructured", "status": "failed",
                              "error_type": type(primary_error).__name__})
        if not settings.ocr_vision_enabled:
            raise
        if guard is not None and guard.ledger.state().unknown_attempts:
            raise OCRProviderError(
                "Unstructured OCR failed; DeepSeek fallback withheld because the shared budget "
                "ledger has an unreconciled provider attempt") from primary_error
        from app.services.ocr_vision import transcribe_pages

        text = transcribe_pages(raw_bytes, filename=filename, guard=guard, telemetry=telemetry)
        if telemetry is not None:
            telemetry.append({"provider": "openrouter", "model": settings.ocr_vision_model,
                              "status": "complete"})
        return text


def _run_unstructured(
    raw_bytes: bytes, *, filename: str, mime_type: str,
    guard: LedgerGuard | None, page_count: int | None,
) -> str:
    if not settings.unstructured_api_key:
        raise OCRProviderError("CURRICULUM_TUTOR_UNSTRUCTURED_API_KEY is required when OCR fallback is triggered")
    boundary = "eduground-ocr-boundary"
    body = _multipart_body(boundary, raw_bytes, filename=filename, mime_type=mime_type)
    headers = {
        "Accept": "application/json",
        "unstructured-api-key": settings.unstructured_api_key,
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    if guard is not None:
        payload = _guarded_extract(guard, raw_bytes, body=body, headers=headers, page_count=page_count)
    else:
        payload = _post_multipart(headers=headers, body=body)
    if not isinstance(payload, list):
        raise OCRProviderError("Unstructured OCR response was malformed")
    text = "\n\n".join((item.get("text") or "").strip() for item in payload if isinstance(item, dict)).strip()
    if not text:
        raise OCRProviderError("Unstructured OCR returned no text")
    return text


def _guarded_extract(
    guard: LedgerGuard, raw_bytes: bytes, *, body: bytes, headers: dict[str, str],
    page_count: int | None,
) -> Any:
    """Reserve from exact page/request extent before the OCR dispatch."""
    schedule = guard.schedule(provider=UNSTRUCTURED_PROVIDER, model=UNSTRUCTURED_SERVICE_ID,
                              operation=OCR_OPERATION)
    rates = schedule.rates()
    pages = 0
    requests_bound = 0
    if "pages" in rates:
        resolved = page_count if isinstance(page_count, int) and not isinstance(page_count, bool) and page_count > 0 \
            else count_pdf_pages(raw_bytes)
        if resolved is None:
            raise LedgerConfigurationError(
                "OCR is priced per page but the page count cannot be determined; refusing to dispatch")
        pages = resolved
    if "requests" in rates:
        requests_bound = 1
    if pages == 0 and requests_bound == 0:
        raise LedgerConfigurationError("OCR price schedule has neither pages nor requests units; refusing to dispatch")
    usage = {"pages": pages, "requests": requests_bound}
    return guard.invoke(
        role=OCR_OPERATION, provider=UNSTRUCTURED_PROVIDER, model=UNSTRUCTURED_SERVICE_ID,
        operation=OCR_OPERATION,
        bounds={"pages": pages, "requests": requests_bound},
        dispatch=lambda: _post_multipart(headers=headers, body=body),
        extract=lambda _payload: (UNSTRUCTURED_SERVICE_ID, usage, None),
    )


def _post_multipart(*, headers: dict[str, str], body: bytes) -> Any:
    req = request.Request(
        settings.unstructured_api_url,
        data=body,
        method="POST",
        headers=headers,
    )
    try:
        with request.urlopen(req, timeout=60.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:  # pragma: no cover - network/provider failure
        detail = exc.read().decode("utf-8", errors="replace")
        raise OCRProviderError(f"Unstructured OCR request failed with status {exc.code}: {detail}") from exc
    except error.URLError as exc:  # pragma: no cover - network/provider failure
        raise OCRProviderError(f"Unstructured OCR request failed: {exc.reason}") from exc
    return payload


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
