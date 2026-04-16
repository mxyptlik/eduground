from __future__ import annotations

import json

import pytest

from app.services import ocr


def test_ocr_trigger_for_empty_text() -> None:
    raw_bytes = b"x" * 1000
    assert ocr.should_run_ocr("", raw_bytes, threshold=0.1) is True


def test_ocr_does_not_trigger_for_non_empty_text() -> None:
    raw_bytes = ("meaningful text " * 40).encode("utf-8")
    assert ocr.should_run_ocr("tiny but present", raw_bytes, threshold=0.9) is False


def test_run_ocr_requires_provider_key(monkeypatch) -> None:
    monkeypatch.setattr(ocr.settings, "unstructured_api_key", None)

    with pytest.raises(ocr.OCRProviderError):
        ocr.run_ocr(b"%PDF-1.4", filename="scan.pdf", mime_type="application/pdf")


def test_run_ocr_returns_provider_text(monkeypatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self) -> bytes:
            return json.dumps([{"text": "Recovered text"}, {"text": "Second block"}]).encode("utf-8")

    monkeypatch.setattr(ocr.settings, "unstructured_api_key", "test-key")
    monkeypatch.setattr(ocr.request, "urlopen", lambda req, timeout: FakeResponse())

    text = ocr.run_ocr(b"%PDF-1.4", filename="scan.pdf", mime_type="application/pdf")

    assert "Recovered text" in text
    assert "Second block" in text
