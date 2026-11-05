"""Offline contract checks for the user-run high-load and corpus profilers."""

from types import SimpleNamespace

from tools.evaluation.find_health_peak import evaluate
from tools.evaluation.profile_course_documents import call_totals, embedding_batches, stage_stats
from tools.evaluation.prepare_profile_prices import merge_registries
from decimal import Decimal
import json


def _limits():
    return SimpleNamespace(p95_ms=1500, max_failure_rate=0.05,
                           min_check_rate=0.99, min_requests_per_vu_second=0.5)


def _metrics(**changes):
    metrics = {
        "http_req_duration": {"p(95)": 400},
        "http_req_failed": {"value": 0},
        "checks": {"value": 1},
        "http_reqs": {"count": 120_000, "rate": 6000},
        "vus": {"max": 8000},
    }
    metrics.update(changes)
    return metrics


def test_peak_pass_requires_measured_vus_latency_and_request_volume():
    status, reasons, observed = evaluate(_metrics(), 8000, 20, _limits())
    assert status == "pass" and reasons == [] and observed["p95_ms"] == 400

    status, reasons, _ = evaluate(_metrics(vus={"max": 5000}, http_reqs={"count": 100}),
                                  8000, 20, _limits())
    assert status == "invalid"
    assert "requested_vus_not_reached" in reasons
    assert any(reason.startswith("request_count_below_") for reason in reasons)


def test_peak_missing_summary_is_inconclusive():
    status, reasons, _ = evaluate({}, 8000, 20, _limits())
    assert status == "invalid" and reasons


def test_stage_means_count_only_measured_documents():
    timing = stage_stats([{"parse_ms": 10.0, "chunk_ms": 2.0},
                          {"parse_ms": 30.0, "chunk_ms": None},
                          {"parse_ms": None, "chunk_ms": 4.0}])
    assert timing["parse_ms"] == {"count": 2, "total_ms": 40.0, "mean_ms": 20.0,
                                   "median_ms": 20.0, "p95_ms": 30.0}
    assert timing["chunk_ms"] == {"count": 2, "total_ms": 6.0, "mean_ms": 3.0,
                                   "median_ms": 3.0, "p95_ms": 4.0}


def test_embedding_batches_never_mix_files_and_respect_count_and_bytes():
    batches = embedding_batches(["abc", "def", "éé", "last"], max_chunks=2, max_bytes=6)
    assert batches == [["abc", "def"], ["éé"], ["last"]]
    assert sum(map(len, batches)) == 4
    try:
        embedding_batches(["too long"], max_chunks=2, max_bytes=2)
    except ValueError as exc:
        assert "above" in str(exc)
    else:
        assert False, "an oversized individual chunk must fail before dispatch"


def test_call_totals_keep_unknown_attempts_separate_from_known_cost():
    calls = [{"role": "ingestion_embedding", "actual_usd": "0.20", "status": "settled"},
             {"role": "ingestion_embedding", "actual_usd": None, "status": "unknown"},
             {"role": "ocr", "actual_usd": "0.30", "status": "settled"}]
    assert call_totals(calls, "ingestion_embedding") == (2, "0.20", 1)
    assert Decimal(call_totals(calls, "ocr")[1]) == Decimal("0.30")


def test_price_merge_deduplicates_existing_and_adds_only_sourced_ocr(tmp_path):
    existing = {"version": "1.0", "schedules": [{"provider": "openrouter", "model": "m",
        "operation": "ingestion_embedding", "effective_from": "2026-09-30T00:00:00+00:00",
        "currency": "USD", "source": "test", "prices": [{"unit": "input_tokens", "amount": "1", "quantity": 1}]}]}
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps(existing), encoding="utf-8")
    second.write_text(json.dumps(existing), encoding="utf-8")
    ocr = {"provider": "unstructured", "model": "hosted-api", "operation": "ocr",
           "effective_from": "2026-09-30T00:00:00+00:00", "currency": "USD", "source": "test contract",
           "prices": [{"unit": "pages", "amount": "0.01", "quantity": 1}]}
    merged = merge_registries([first, second], ocr_price=ocr)
    assert len(merged["schedules"]) == 2
    assert merged["schedules"][1] == ocr
