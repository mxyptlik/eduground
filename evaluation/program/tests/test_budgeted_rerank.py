from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from evaluation.program.budget import BudgetError, BudgetLedger
from evaluation.program.budgeted_rerank import BudgetedRerankClient, build_budgeted_reranker
from evaluation.program.config import EvaluationSettings
from evaluation.program.pricing import PriceRegistry, PriceSchedule, UnitPrice


MODEL = "openai/gpt-4.1-mini"


def _prices():
    return PriceRegistry("1.0", (PriceSchedule(
        "openrouter", MODEL, "rerank", datetime(2026, 1, 1, tzinfo=timezone.utc),
        "USD", "fixture-only", (UnitPrice("input_tokens", Decimal("1"), 1_000_000),
                               UnitPrice("output_tokens", Decimal("1"), 1_000_000)),
    ),))


class FakeClient:
    def __init__(self, usage=True):
        self.calls = 0
        self.usage = usage

    def post(self, url, *, headers, json):
        self.calls += 1
        payload = {"id": "rank-1", "model": MODEL,
                   "choices": [{"message": {"content": '{"ranked_indices":[{"index":0,"score":0.9}]}'}}]}
        if self.usage:
            payload["usage"] = {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.001}
        return httpx.Response(200, json=payload)

    def close(self):
        pass


def test_existing_reranker_uses_bounded_client_and_records_cost(tmp_path):
    settings = EvaluationSettings(_env_file=None, max_api_spend_usd=10,
                                  reranker_model=MODEL, reranker_max_output_tokens=128)
    ledger = BudgetLedger(tmp_path / "budget.sqlite", 10)
    provider = build_budgeted_reranker(settings, ledger=ledger, prices=_prices(),
        max_input_tokens=8192,
        environ={"CURRICULUM_TUTOR_OPENROUTER_API_KEY": "fixture-only"})
    fake = FakeClient()
    provider._client.inner = fake
    result = provider.rerank_with_result("Which passage?", [{"source_id": "s1", "chunk_id": "c1",
                                                              "text": "A relevant passage."}], strict=True)
    assert result.response_model == MODEL and result.ranked_coverage == 1
    assert fake.calls == 1 and ledger.state().by_role_usd == {"rerank": Decimal("0.001")}
    assert provider._client.attempts[0]["actual_usd"] == "0.001"


def test_missing_usage_blocks_next_rerank_attempt(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite", 10)
    fake = FakeClient(usage=False)
    client = BudgetedRerankClient(fake, ledger=ledger, prices=_prices(), model_id=MODEL,
                                  max_input_tokens=4096, max_output_tokens=128)
    payload = {"model": MODEL, "messages": [{"role": "user", "content": "Rank."}],
               "max_tokens": 128, "usage": {"include": True}}
    with pytest.raises(BudgetError, match="unknown"):
        client.post("https://openrouter.ai/api/v1/chat/completions", headers={}, json=payload)
    with pytest.raises(BudgetError, match="unreconciled"):
        client.post("https://openrouter.ai/api/v1/chat/completions", headers={}, json=payload)
    assert fake.calls == 1 and ledger.state().unknown_attempts == 1
