from __future__ import annotations

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from importlib.metadata import PackageNotFoundError, version

from evaluation.program.budget import BudgetLedger
from evaluation.program.config import EvaluationSettings, RunConfigurationError
from evaluation.program.pricing import PriceRegistry, PriceSchedule, UnitPrice
from evaluation.program.ragas_judge import build_deepseek_ragas_judge


def test_judge_rejects_missing_key_and_insecure_gateway_without_importing_ragas():
    settings = EvaluationSettings(_env_file=None)
    with pytest.raises(RunConfigurationError, match="API key"):
        build_deepseek_ragas_judge(settings, api_key="")
    with pytest.raises(RunConfigurationError, match="HTTPS"):
        build_deepseek_ragas_judge(settings, api_key="fake", base_url="http://example.test")


def test_budgeted_judge_builds_without_network(tmp_path):
    try:
        installed = version("ragas")
    except PackageNotFoundError:
        pytest.skip("RAGAS environment unavailable")
    if installed != "0.4.3":
        pytest.skip("test requires RAGAS 0.4.3")
    settings = EvaluationSettings(_env_file=None, max_api_spend_usd=10)
    ledger = BudgetLedger(tmp_path / "budget.sqlite", 10)
    prices = PriceRegistry("1.0", (PriceSchedule(
        "openrouter", settings.judge_model, "judge", datetime(2026, 1, 1, tzinfo=timezone.utc),
        "USD", "fixture-only", (UnitPrice("input_tokens", Decimal("1"), 1_000_000),
                               UnitPrice("output_tokens", Decimal("1"), 1_000_000)),
    ),))
    judge = build_deepseek_ragas_judge(settings, api_key="fixture-only", ledger=ledger,
                                       prices=prices, max_input_tokens=8192)
    assert judge.budget_instrumented is True
    assert judge.transport is not None and judge.llm.is_async
    assert ledger.state().settled_usd == 0
