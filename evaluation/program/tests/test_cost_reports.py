from datetime import datetime, timezone
from decimal import Decimal

from evaluation.program.cost_reports import CostedAttempt, build_cost_report
from evaluation.program.pricing import CallUsage, PriceRegistry, PriceSchedule, UnitPrice


def _prices():
    return PriceRegistry("1.0", (
        PriceSchedule("openrouter", "deepseek/deepseek-v4.1-flash", "judge",
            datetime(2026, 1, 1, tzinfo=timezone.utc), "USD", "fixture://test-price",
            (UnitPrice("requests", Decimal("0.5"), 1),)),
    ))


def _usage(call_id, provider="openrouter", model="deepseek/deepseek-v4.1-flash"):
    return CallUsage(call_id, provider, model, "judge", datetime(2026, 9, 30, tzinfo=timezone.utc), requests=1)


def test_individual_roles_and_failed_attempts_are_counted():
    rows = [
        CostedAttempt(_usage("j1"), "evaluation", "deepseek_judge", "c1", "failure"),
        CostedAttempt(_usage("j2"), "evaluation", "deepseek_judge", "c1", "success"),
    ]
    report = build_cost_report(rows, _prices())
    assert report["categories"]["evaluation"]["amount"] == "1.0"
    assert report["overall"]["failed_call_count"] == 1
    assert report["roles"][0]["role"] == "deepseek_judge"
    assert report["roles"][0]["call_count"] == 2


def test_unknown_provider_cost_does_not_disappear():
    rows = [CostedAttempt(_usage("a1", provider="gemini", model="gemini-3.5-flash"),
                         "serving", "gemini_answer", "c1", "success")]
    report = build_cost_report(rows, _prices())
    assert report["overall"]["amount"] is None
    assert report["overall"]["unknown_call_ids"] == ["a1"]
    assert report["categories"]["serving"]["call_count"] == 1
