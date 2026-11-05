from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from evaluation.program.budget import BudgetError, BudgetLedger
from evaluation.program.budgeted_call import invoke_budgeted_role, maximum_call_cost
from evaluation.program.config import EvaluationSettings
from evaluation.program.pricing import PriceRegistry, PriceSchedule, UnitPrice
from evaluation.program.providers import ProviderRoutingError


def _prices(model="gemini-3.5-flash", units=None):
    return PriceRegistry("1.0", (PriceSchedule("gemini", model, "answer",
        datetime(2026, 1, 1, tzinfo=timezone.utc), "USD", "fixture://price",
        units or (UnitPrice("requests", Decimal("0.50"), 1),)),))


class FakeProvider:
    def __init__(self, *, model="gemini-3.5-flash", error=False):
        self.model = model
        self.error = error
        self.calls = 0

    def generate_messages_with_result(self, messages):
        self.calls += 1
        if self.error:
            raise RuntimeError("network attempt failed")
        return SimpleNamespace(gateway="gemini", response_model=self.model,
                               request_id="p1", usage={"promptTokenCount": 5,
                               "cachedContentTokenCount": 0, "candidatesTokenCount": 2,
                               "thoughtsTokenCount": 0}, text="Answer")


def _settings():
    return EvaluationSettings(_env_file=None, max_api_spend_usd=10)


def test_maximum_reservation_uses_more_expensive_cache_tariff():
    schedule = _prices(units=(UnitPrice("input_tokens", Decimal("1"), 100),
                              UnitPrice("cached_input_tokens", Decimal("2"), 100),
                              UnitPrice("output_tokens", Decimal("3"), 100),
                              UnitPrice("requests", Decimal("0.1"), 1))).schedules[0]
    assert maximum_call_cost(schedule, max_input_tokens=100, max_output_tokens=10) == Decimal("2.4")


def test_call_reserves_then_records_actual_and_role_expense(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite3", 10)
    provider = FakeProvider()
    record = invoke_budgeted_role(_settings(), "answer", provider,
        [{"role": "user", "content": "Q"}], ledger=ledger, prices=_prices(),
        max_input_tokens=2048, max_output_tokens=20)
    assert provider.calls == 1
    assert record.cost.amount == Decimal("0.5")
    assert ledger.state().by_role_usd["answer"] == Decimal("0.5")


def test_budget_denial_prevents_dispatch(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite3", "0.10")
    provider = FakeProvider()
    with pytest.raises(BudgetError, match="cap"):
        invoke_budgeted_role(EvaluationSettings(_env_file=None, max_api_spend_usd=Decimal("0.10")), "answer", provider,
            [], ledger=ledger, prices=_prices(), max_input_tokens=2048, max_output_tokens=20)
    assert provider.calls == 0


def test_failed_attempt_or_model_swap_is_recorded_and_stops(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite3", 10)
    with pytest.raises(RuntimeError):
        invoke_budgeted_role(_settings(), "answer", FakeProvider(error=True),
            [], ledger=ledger, prices=_prices(), max_input_tokens=2048, max_output_tokens=20)
    assert ledger.state().unknown_attempts == 1
    other = BudgetLedger(tmp_path / "other.sqlite3", 10)
    prices = PriceRegistry("1.0", _prices().schedules + _prices("gemini-other").schedules)
    with pytest.raises(ProviderRoutingError):
        invoke_budgeted_role(_settings(), "answer", FakeProvider(model="gemini-other"),
            [], ledger=other, prices=prices, max_input_tokens=2048, max_output_tokens=20)
    assert other.state().unknown_attempts == 0


def test_unbounded_prompt_or_mismatched_run_cap_is_rejected_before_dispatch(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite3", 10)
    provider = FakeProvider()
    with pytest.raises(BudgetError, match="explicit USD cap"):
        invoke_budgeted_role(EvaluationSettings(_env_file=None), "answer", provider,
            [], ledger=ledger, prices=_prices(), max_input_tokens=2048, max_output_tokens=20)
    with pytest.raises(BudgetError, match="prompt bound"):
        invoke_budgeted_role(_settings(), "answer", provider,
            [{"role": "user", "content": "Q"}], ledger=ledger, prices=_prices(),
            max_input_tokens=1, max_output_tokens=20)
    assert provider.calls == 0


def test_reported_openrouter_cost_can_settle_when_cache_breakdown_is_absent(tmp_path):
    from evaluation.program.budgeted_call import _reported_openrouter_cost
    reported = _reported_openrouter_cost(
        SimpleNamespace(gateway="openrouter", usage={"cost": 0.002}), "call-1")
    assert reported.amount == Decimal("0.002")
    assert reported.price_source == "OpenRouter response usage.cost"
    assert _reported_openrouter_cost(SimpleNamespace(gateway="gemini", usage={"cost": 0.002}), "call-2") is None
