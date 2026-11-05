"""Offline contracts for DeepSeek page-text fallback; no provider calls."""

from types import SimpleNamespace

import fitz
import pytest

from app.services import ocr, ocr_vision
from app.services.parsing import parse_source_bytes


def _pdf() -> bytes:
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "The visible course sentence")
        return document.tobytes()


def test_missing_unstructured_uses_deepseek_page_transcription(monkeypatch):
    monkeypatch.setattr(ocr.settings, "unstructured_api_key", None)
    monkeypatch.setattr(ocr.settings, "ocr_vision_enabled", True)
    monkeypatch.setattr(ocr.settings, "ocr_vision_model", "deepseek/deepseek-v4.1-flash")
    monkeypatch.setattr(ocr.settings, "openrouter_api_key", "offline-test-key")
    monkeypatch.setattr(ocr_vision, "_post", lambda payload: {
        "model": "deepseek/deepseek-v4.1-flash",
        "choices": [{"finish_reason": "stop", "message": {"content": "The visible course sentence"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 8, "cost": 0.00001},
    })
    events = []
    text = ocr.run_ocr(_pdf(), filename="course.pdf", mime_type="application/pdf", telemetry=events)
    assert text == "Page 1\nThe visible course sentence"
    reparsed = parse_source_bytes(text.encode("utf-8"), "course.pdf.ocr.txt")
    assert {segment.segment_number for segment in reparsed.segments} == {1}
    assert events[0]["provider"] == "unstructured" and events[0]["status"] == "failed"
    assert events[1]["page"] == 1 and events[1]["image_bytes"] > 0
    assert events[-1]["model"] == "deepseek/deepseek-v4.1-flash"


def test_unknown_primary_spend_blocks_secondary_call(monkeypatch):
    monkeypatch.setattr(ocr.settings, "unstructured_api_key", None)
    monkeypatch.setattr(ocr.settings, "ocr_vision_enabled", True)
    guard = SimpleNamespace(ledger=SimpleNamespace(state=lambda: SimpleNamespace(unknown_attempts=1)))
    monkeypatch.setattr(ocr_vision, "transcribe_pages", lambda *args, **kwargs:
                        pytest.fail("fallback must not dispatch while spend is unknown"))
    with pytest.raises(ocr.OCRProviderError, match="unreconciled"):
        ocr.run_ocr(b"irrelevant", filename="course.pdf", mime_type="application/pdf", guard=guard)


def test_guarded_deepseek_call_records_pinned_model_and_page_bounds(monkeypatch):
    monkeypatch.setattr(ocr_vision.settings, "ocr_vision_model", "deepseek/deepseek-v4.1-flash")
    monkeypatch.setattr(ocr_vision.settings, "openrouter_api_key", "offline-test-key")
    calls = []
    response = {"model": "deepseek/deepseek-v4.1-flash",
                "choices": [{"finish_reason": "stop", "message": {"content": "Page text"}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 2, "cost": 0.00001}}

    def invoke(**kwargs):
        calls.append(kwargs)
        identity, usage, cost = kwargs["extract"](response)
        assert identity == "deepseek/deepseek-v4.1-flash"
        assert usage["input_tokens"] == 120 and cost is not None
        return response

    text = ocr_vision.transcribe_pages(_pdf(), filename="course.pdf",
                                      guard=SimpleNamespace(invoke=invoke))
    assert text == "Page 1\nPage text"
    assert calls[0]["operation"] == "ocr_vision"
    assert calls[0]["model"] == "deepseek/deepseek-v4.1-flash"
    assert calls[0]["bounds"]["max_input_tokens"] > 1000


def test_truncated_page_is_rejected():
    with pytest.raises(ocr_vision.OCRVisionError, match="did not finish"):
        ocr_vision._response_text({"choices": [{"finish_reason": "length",
                                                 "message": {"content": "Partial page"}}]})
