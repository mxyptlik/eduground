"""Guarded ingestion dispatches: embeddings and OCR under a shared ledger."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.ledger import BudgetLedger, LedgerConfigurationError, LedgerError, LedgerGuard, PriceRegistry
from app.services import embeddings, ocr


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def make_guard(tmp_path, *, provider="openrouter", model="pinned/model", operation="ingestion_embedding",
               units=(("input_tokens", "2", 1_000_000),), cap="10"):
    payload = {
        "version": "1.0",
        "schedules": [{
            "provider": provider, "model": model, "operation": operation,
            "effective_from": "2026-01-01T00:00:00+00:00", "currency": "USD", "source": "fixture-only",
            "prices": [{"unit": unit, "amount": amount, "quantity": quantity}
                       for unit, amount, quantity in units],
        }],
    }
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return LedgerGuard(BudgetLedger(tmp_path / "budget.sqlite3", cap), PriceRegistry.from_json(path))


@pytest.fixture
def embedding_settings(monkeypatch):
    monkeypatch.setattr(embeddings.settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(embeddings.settings, "openrouter_embedding_model", "pinned/model")
    monkeypatch.setattr(embeddings.settings, "gemini_fallback_enabled", True)
    monkeypatch.setattr(embeddings.settings, "gemini_api_key", "test-gemini")


def test_guarded_embedding_settles_provider_reported_cost(tmp_path, monkeypatch, embedding_settings):
    guard = make_guard(tmp_path)
    monkeypatch.setattr(embeddings.request, "urlopen", lambda req, timeout: FakeResponse({
        "model": "pinned/model",
        "data": [{"embedding": [0.1, 0.2]}],
        "usage": {"prompt_tokens": 10, "cost": 0.0004},
    }))
    vectors = embeddings.embed_texts(["hello"], guard=guard)
    assert vectors == [[0.1, 0.2]]
    assert guard.ledger.state().settled_usd == Decimal("0.0004")


def test_guarded_embedding_estimates_from_verified_tokens(tmp_path, monkeypatch, embedding_settings):
    guard = make_guard(tmp_path)
    monkeypatch.setattr(embeddings.request, "urlopen", lambda req, timeout: FakeResponse({
        "model": "pinned/model",
        "data": [{"embedding": [1.0]}],
        "usage": {"prompt_tokens": 1000},
    }))
    embeddings.embed_texts(["hello"], guard=guard)
    assert guard.ledger.state().settled_usd == Decimal("0.002")


def test_guarded_embedding_rejects_model_drift_and_blocks_further_calls(tmp_path, monkeypatch, embedding_settings):
    guard = make_guard(tmp_path)
    monkeypatch.setattr(embeddings.request, "urlopen", lambda req, timeout: FakeResponse({
        "model": "some/other-model",
        "data": [{"embedding": [0.1]}],
        "usage": {"prompt_tokens": 10},
    }))
    with pytest.raises(LedgerError, match="not the pinned"):
        embeddings.embed_texts(["hello"], guard=guard)
    assert guard.ledger.state().unknown_attempts == 1
    with pytest.raises(LedgerError, match="unreconciled usage"):
        guard.ledger.reserve("next", "ingestion_embedding", "0.1")


def test_guarded_embedding_missing_usage_is_unknown_not_free(tmp_path, monkeypatch, embedding_settings):
    guard = make_guard(tmp_path)
    monkeypatch.setattr(embeddings.request, "urlopen", lambda req, timeout: FakeResponse({
        "model": "pinned/model",
        "data": [{"embedding": [0.1]}],
    }))
    with pytest.raises(LedgerError):
        embeddings.embed_texts(["hello"], guard=guard)
    assert guard.ledger.state().unknown_attempts == 1


def test_guarded_embedding_failure_never_falls_back(tmp_path, monkeypatch, embedding_settings):
    from urllib import error

    guard = make_guard(tmp_path)
    monkeypatch.setattr(embeddings.request, "urlopen",
                        lambda req, timeout: (_ for _ in ()).throw(error.URLError("boom")))
    monkeypatch.setattr(embeddings, "_embed_with_gemini",
                        lambda texts: (_ for _ in ()).throw(AssertionError("silent fallback")))
    with pytest.raises(embeddings.EmbeddingProviderError):
        embeddings.embed_texts(["hello"], guard=guard)
    assert guard.ledger.state().unknown_attempts == 1


def test_unguarded_embedding_keeps_fallback_behaviour(tmp_path, monkeypatch, embedding_settings):
    from urllib import error

    monkeypatch.setattr(embeddings.request, "urlopen",
                        lambda req, timeout: (_ for _ in ()).throw(error.URLError("boom")))
    monkeypatch.setattr(embeddings, "_embed_with_gemini", lambda texts: [[9.9] for _ in texts])
    assert embeddings.embed_texts(["hello"]) == [[9.9]]


def _ocr_guard(tmp_path, units):
    return make_guard(tmp_path, provider="unstructured", model="hosted-api", operation="ocr", units=units)


def test_guarded_ocr_reserves_exact_pages_and_settles(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr.settings, "unstructured_api_key", "test-key")
    guard = _ocr_guard(tmp_path, (("pages", "0.01", 1),))
    monkeypatch.setattr(ocr.request, "urlopen",
                        lambda req, timeout: FakeResponse([{"text": "Recovered"}, {"text": "Second"}]))
    text = ocr.run_ocr(b"%PDF-1.4 scanned", filename="scan.pdf", mime_type="application/pdf",
                       guard=guard, page_count=2)
    assert "Recovered" in text and "Second" in text
    assert guard.ledger.state().settled_usd == Decimal("0.02")


def test_guarded_ocr_refuses_unknown_page_extent_before_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr.settings, "unstructured_api_key", "test-key")
    guard = _ocr_guard(tmp_path, (("pages", "0.01", 1),))
    monkeypatch.setattr(ocr.request, "urlopen",
                        lambda req, timeout: (_ for _ in ()).throw(AssertionError("must not dispatch")))
    with pytest.raises(LedgerConfigurationError, match="page count cannot be determined"):
        ocr.run_ocr(b"opaque bytes", filename="scan.bin", mime_type="application/octet-stream", guard=guard)
    assert guard.ledger.state().reserved_usd == 0 and guard.ledger.state().settled_usd == 0


def test_guarded_ocr_request_pricing_bounds_one_request(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr.settings, "unstructured_api_key", "test-key")
    guard = _ocr_guard(tmp_path, (("requests", "0.05", 1),))
    monkeypatch.setattr(ocr.request, "urlopen", lambda req, timeout: FakeResponse([{"text": "ok"}]))
    ocr.run_ocr(b"opaque bytes", filename="scan.bin", mime_type="application/octet-stream", guard=guard)
    assert guard.ledger.state().settled_usd == Decimal("0.05")


def test_guarded_ocr_without_price_schedule_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr.settings, "unstructured_api_key", "test-key")
    guard = make_guard(tmp_path)  # only an OpenRouter embedding schedule exists
    monkeypatch.setattr(ocr.request, "urlopen",
                        lambda req, timeout: (_ for _ in ()).throw(AssertionError("must not dispatch")))
    with pytest.raises(LedgerConfigurationError, match="price schedule missing"):
        ocr.run_ocr(b"%PDF-1.4 scanned", filename="invoice.pdf", mime_type="application/pdf",
                    guard=guard, page_count=1)
