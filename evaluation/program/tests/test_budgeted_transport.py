import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json

import httpx
import pytest

from evaluation.program.budget import BudgetError, BudgetLedger
from evaluation.program.budgeted_transport import BudgetedOpenRouterTransport
from evaluation.program.pricing import PriceRegistry, PriceSchedule, UnitPrice


MODEL = "deepseek/deepseek-v4.1-flash"


def _prices():
    return PriceRegistry("1.0", (PriceSchedule(
        "openrouter", MODEL, "judge", datetime(2026, 1, 1, tzinfo=timezone.utc),
        "USD", "fixture-only", (UnitPrice("input_tokens", Decimal("1"), 1_000_000),
                               UnitPrice("output_tokens", Decimal("1"), 1_000_000)),
    ),))


def _request():
    return httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions",
                         json={"model": MODEL, "messages": [{"role": "user", "content": "Evaluate."}],
                               "max_tokens": 100})


def _response(*, model=MODEL, usage=None):
    if usage is None:
        usage = {"prompt_tokens": 10, "completion_tokens": 20, "cost": 0.001}
    return {"id": "completion-1", "model": model, "usage": usage,
            "choices": [{"message": {"content": "{}"}}]}


def test_each_instructor_retry_reserves_and_records_separate_attempt(tmp_path):
    seen = []

    async def reply(request):
        seen.append(json.loads(await request.aread()))
        return httpx.Response(200, json=_response())

    ledger = BudgetLedger(tmp_path / "budget.sqlite", "0.01")
    transport = BudgetedOpenRouterTransport(inner=httpx.MockTransport(reply), ledger=ledger,
        prices=_prices(), model_id=MODEL, max_input_tokens=4096, max_output_tokens=100)

    async def calls():
        await transport.handle_async_request(_request())
        await transport.handle_async_request(_request())
        await transport.aclose()

    asyncio.run(calls())
    assert len(seen) == 2 and all(payload["usage"] == {"include": True} for payload in seen)
    assert ledger.state().settled_usd == Decimal("0.002")
    assert ledger.state().by_role_usd == {"judge": Decimal("0.002")}
    assert len({entry["call_id"] for entry in transport.attempts}) == 2
    assert all("messages" not in entry for entry in transport.attempts)


def test_unknown_usage_halts_following_attempt_before_network(tmp_path):
    dispatched = []

    async def reply(request):
        dispatched.append(1)
        return httpx.Response(200, json=_response(usage={"prompt_tokens": 10}))

    ledger = BudgetLedger(tmp_path / "budget.sqlite", "0.01")
    transport = BudgetedOpenRouterTransport(inner=httpx.MockTransport(reply), ledger=ledger,
        prices=_prices(), model_id=MODEL, max_input_tokens=4096, max_output_tokens=100)

    async def calls():
        with pytest.raises(BudgetError, match="unknown"):
            await transport.handle_async_request(_request())
        with pytest.raises(BudgetError, match="unreconciled"):
            await transport.handle_async_request(_request())

    asyncio.run(calls())
    assert len(dispatched) == 1
    assert ledger.state().unknown_attempts == 1


def test_wrong_model_is_recorded_then_blocks_judge_result(tmp_path):
    async def reply(request):
        return httpx.Response(200, json=_response(model="different/model"))

    ledger = BudgetLedger(tmp_path / "budget.sqlite", "0.01")
    transport = BudgetedOpenRouterTransport(inner=httpx.MockTransport(reply), ledger=ledger,
        prices=_prices(), model_id=MODEL, max_input_tokens=4096, max_output_tokens=100)

    async def call():
        with pytest.raises(BudgetError, match="different model"):
            await transport.handle_async_request(_request())

    asyncio.run(call())
    assert ledger.state().settled_usd == Decimal("0.001")
