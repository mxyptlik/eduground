"""Worker-side shared ledger: caps, unknown blocking, prices and env gating."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.ledger import (
    CAP_ENV,
    LEDGER_ENV,
    REGISTRY_ENV,
    BudgetLedger,
    LedgerConfigurationError,
    LedgerError,
    PriceRegistry,
    count_pdf_pages,
    load_ledger_guard,
    maximum_cost,
    settlement_cost,
)


def _registry_payload(*, provider="openrouter", model="pinned/model", operation="ingestion_embedding",
                      units=(("input_tokens", "2", 1_000_000),), currency="USD",
                      effective_from="2026-01-01T00:00:00+00:00"):
    return {
        "version": "1.0",
        "schedules": [{
            "provider": provider, "model": model, "operation": operation,
            "effective_from": effective_from, "currency": currency, "source": "fixture-only",
            "prices": [{"unit": unit, "amount": amount, "quantity": quantity}
                       for unit, amount, quantity in units],
        }],
    }


def _registry_file(tmp_path, **kwargs):
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(_registry_payload(**kwargs)), encoding="utf-8")
    return path


def test_reserve_settle_cap_and_unknown_blocking(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite3", 10)
    ledger.reserve("a", "ingestion_embedding", "6")
    ledger.settle("a", "2")
    with pytest.raises(LedgerError, match="cap would be exceeded"):
        ledger.reserve("b", "ocr", "9")
    ledger.reserve("b", "ocr", "5")

    ledger.reserve("c", "ocr", "1")
    ledger.settle("c", None)
    with pytest.raises(LedgerError, match="unreconciled usage"):
        ledger.reserve("d", "ocr", "1")

    ledger.settle("c", "0.1")  # explicit reconciliation reopens dispatch
    state = ledger.state()
    assert state.settled_usd == Decimal("2.1")
    assert state.unknown_attempts == 0
    assert state.by_role_usd["ocr"] == Decimal("5.1")


def test_duplicate_ids_and_cap_reopen_are_rejected(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite3", 10)
    ledger.reserve("a", "ocr", "1")
    with pytest.raises(LedgerError, match="duplicate call_id"):
        ledger.reserve("a", "ocr", "1")
    with pytest.raises(LedgerError, match="different cap"):
        BudgetLedger(tmp_path / "budget.sqlite3", 20)


def test_registry_selects_latest_effective_and_rejects_unsupported_units(tmp_path):
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({
        "version": "1.0",
        "schedules": [
            {**_registry_payload()["schedules"][0], "effective_from": "2026-01-01T00:00:00+00:00",
             "prices": [{"unit": "input_tokens", "amount": "1", "quantity": 1000000}]},
            {**_registry_payload()["schedules"][0], "effective_from": "2026-06-01T00:00:00+00:00",
             "prices": [{"unit": "input_tokens", "amount": "2", "quantity": 1000000}]},
        ],
    }), encoding="utf-8")
    registry = PriceRegistry.from_json(path)
    from datetime import datetime, timezone

    early = registry.schedule_for("openrouter", "pinned/model", "ingestion_embedding",
                                  datetime(2026, 3, 1, tzinfo=timezone.utc))
    late = registry.schedule_for("openrouter", "pinned/model", "ingestion_embedding",
                                 datetime(2026, 7, 1, tzinfo=timezone.utc))
    assert early.rates()["input_tokens"] == Decimal("1") / 1000000
    assert late.rates()["input_tokens"] == Decimal("2") / 1000000
    assert registry.schedule_for("openrouter", "missing/model", "ingestion_embedding",
                                 datetime(2026, 7, 1, tzinfo=timezone.utc)) is None

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_registry_payload(units=(("characters", "1", 1000),))), encoding="utf-8")
    with pytest.raises(LedgerConfigurationError, match="unsupported price units"):
        PriceRegistry.from_json(bad)


def test_maximum_and_settlement_cost_math(tmp_path):
    registry = PriceRegistry.from_json(_registry_file(
        tmp_path,
        units=(("input_tokens", "2", 1_000_000), ("output_tokens", "4", 1_000_000),
               ("requests", "0.001", 1)),
    ))
    from datetime import datetime, timezone

    schedule = registry.schedule_for("openrouter", "pinned/model", "ingestion_embedding",
                                     datetime(2026, 7, 1, tzinfo=timezone.utc))
    assert maximum_cost(schedule, max_input_tokens=1000, requests=1) == (
        Decimal("0.002") + Decimal("0.001"))
    assert settlement_cost(schedule, {"input_tokens": 500, "output_tokens": 0, "requests": 1}) == (
        Decimal("0.001") + Decimal("0.001"))
    with pytest.raises(LedgerError, match="priced unit output_tokens"):
        settlement_cost(schedule, {"input_tokens": 500, "requests": 1})


def test_load_guard_env_gating(tmp_path):
    assert load_ledger_guard({}) is None
    ledger_path = tmp_path / "budget.sqlite3"
    with pytest.raises(LedgerConfigurationError, match=CAP_ENV):
        load_ledger_guard({LEDGER_ENV: str(ledger_path)})
    with pytest.raises(LedgerConfigurationError, match=REGISTRY_ENV):
        load_ledger_guard({LEDGER_ENV: str(ledger_path), CAP_ENV: "10"})
    guard = load_ledger_guard({LEDGER_ENV: str(ledger_path), CAP_ENV: "10",
                               REGISTRY_ENV: str(_registry_file(tmp_path))})
    assert guard is not None and guard.ledger.cap_usd == Decimal("10")
    with pytest.raises(LedgerError, match="different cap"):
        load_ledger_guard({LEDGER_ENV: str(ledger_path), CAP_ENV: "20",
                           REGISTRY_ENV: str(_registry_file(tmp_path))})


def test_count_pdf_pages_reads_real_pages_and_rejects_non_pdf():
    from io import BytesIO

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_blank_page(width=72, height=72)
    buffer = BytesIO()
    writer.write(buffer)
    assert count_pdf_pages(buffer.getvalue()) == 2
    assert count_pdf_pages(b"not a pdf at all") is None
