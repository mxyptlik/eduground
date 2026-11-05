from datetime import datetime, timezone
from decimal import Decimal

import pytest

from evaluation.program.budget import BudgetError, BudgetLedger
from evaluation.program.budgeted_embeddings import build_budgeted_embedding_provider
from evaluation.program.config import EvaluationSettings
from evaluation.program.pricing import PriceRegistry, PriceSchedule, UnitPrice


MODEL = "openai/text-embedding-3-small"


def _prices():
    return PriceRegistry("1.0", tuple(PriceSchedule(
        "openrouter", MODEL, operation, datetime(2026, 1, 1, tzinfo=timezone.utc),
        "USD", "fixture-only", (UnitPrice("input_tokens", Decimal("1"), 1_000_000),),
    ) for operation in ("query_embedding", "relevance_embedding")))


def _settings():
    return EvaluationSettings(_env_file=None, max_api_spend_usd=10,
        embedding_provider="openrouter", embedding_model=MODEL,
        relevance_embedding_provider="openrouter", relevance_embedding_model=MODEL)


def test_query_and_relevance_embeddings_share_one_ledger_without_fallback(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite", 10)
    calls = []
    for operation in ("query_embedding", "relevance_embedding"):
        provider, guarded = build_budgeted_embedding_provider(
            _settings(), operation=operation, ledger=ledger, prices=_prices(),
            environ={"CURRICULUM_TUTOR_OPENROUTER_API_KEY": "fixture-only"})

        def reply(url, payload, headers, timeout_seconds, *, provider="openrouter"):
            calls.append((provider, payload["model"]))
            return {"id": "emb-1", "model": MODEL, "usage": {"prompt_tokens": 10,
                    "total_tokens": 10}, "data": [{"index": 0, "embedding": [0.1, 0.2]}]}

        guarded.inner = reply
        assert provider.fallback is None
        assert provider.embed(["A query"])[0] == [0.1, 0.2]
        assert guarded.attempts[0]["cost_basis"] == "dated_price_estimate"
    assert calls == [("openrouter", MODEL), ("openrouter", MODEL)]
    assert ledger.state().by_role_usd == {"query_embedding": Decimal("0.00001"),
                                          "relevance_embedding": Decimal("0.00001")}


def test_embedding_model_mismatch_blocks_following_call(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite", 10)
    provider, guarded = build_budgeted_embedding_provider(
        _settings(), operation="query_embedding", ledger=ledger, prices=_prices(),
        environ={"CURRICULUM_TUTOR_OPENROUTER_API_KEY": "fixture-only"})
    count = 0

    def wrong(url, payload, headers, timeout_seconds, *, provider="openrouter"):
        nonlocal count
        count += 1
        return {"model": "wrong/model", "usage": {"prompt_tokens": 10},
                "data": [{"index": 0, "embedding": [0.1]}]}

    guarded.inner = wrong
    with pytest.raises(BudgetError, match="verifiable"):
        provider.embed(["A query"])
    with pytest.raises(BudgetError, match="unreconciled"):
        provider.embed(["A query"])
    assert count == 1
