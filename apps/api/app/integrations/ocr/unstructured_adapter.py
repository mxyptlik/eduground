from __future__ import annotations

import json

import httpx

from app.core.config import settings
from app.core.errors import DependencyUnavailableError, ProviderRequestError
from app.core.observability import traced_operation
from app.integrations.ocr.base import OCRAdapter


class OCRProviderError(ProviderRequestError):
    def __init__(self, message: str, *, status_code: int = 502, retryable: bool = True, details: dict | None = None) -> None:
        super().__init__(
            code="unstructured_ocr_error",
            message=message,
            status_code=status_code,
            retryable=retryable,
            provider="unstructured",
            details=details or {},
        )


class UnstructuredOCRAdapter(OCRAdapter):
    def __init__(self) -> None:
        if not settings.unstructured_api_key:
            raise DependencyUnavailableError(
                code="unstructured_api_key_missing",
                message="CURRICULUM_TUTOR_UNSTRUCTURED_API_KEY is required for OCR fallback",
                provider="unstructured",
            )
        self._client = httpx.Client(timeout=120.0)

    def extract_text(self, raw_bytes: bytes, *, filename: str, mime_type: str) -> str:
        files = {
            "files": (filename, raw_bytes, mime_type or "application/octet-stream"),
        }
        data = {
            "strategy": settings.unstructured_ocr_strategy,
            "split_pdf_page": "false",
            "coordinates": "false",
        }
        with traced_operation(
            "provider.unstructured.ocr.request",
            metric_name="eduground_provider_call",
            metric_labels={"provider": "unstructured", "operation": "ocr.extract_text"},
        ):
            response = self._client.post(
                settings.unstructured_api_url,
                headers={
                    "Accept": "application/json",
                    "unstructured-api-key": settings.unstructured_api_key,
                },
                data=data,
                files=files,
            )
        if response.status_code >= 400:
            raise OCRProviderError(
                f"Unstructured OCR request failed: {response.status_code} {response.text}",
                status_code=502 if response.status_code >= 500 else 424,
                retryable=response.status_code >= 500 or response.status_code == 429,
                details={"upstream_status_code": response.status_code},
            )
        payload = response.json()
        if not isinstance(payload, list):
            raise OCRProviderError("Unstructured OCR response was not a list")
        texts: list[str] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
        if not texts:
            raise OCRProviderError(f"Unstructured OCR returned no text: {json.dumps(payload)[:500]}")
        return "\n\n".join(texts)
