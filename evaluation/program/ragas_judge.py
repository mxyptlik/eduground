"""Pinned DeepSeek/OpenRouter judge for the repository's RAGAS 0.4.3 API.

Construction is offline. A guarded variant reserves each underlying
Instructor/OpenRouter attempt and records returned usage/routing. Live use
still requires a current sourced price registry and all other paid roles to
share the same ledger before claiming a run-wide spend ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from decimal import Decimal
from typing import Any

from evaluation.program.budget import BudgetLedger
from evaluation.program.config import EvaluationSettings, RunConfigurationError
from evaluation.program.pricing import PriceRegistry
from evaluation.program.providers import _validate_role


@dataclass(frozen=True)
class RagasJudge:
    llm: Any
    client: Any
    model_id: str
    gateway: str = "openrouter"
    budget_instrumented: bool = False
    transport: Any | None = None


def build_deepseek_ragas_judge(
    settings: EvaluationSettings, *, api_key: str,
    base_url: str = "https://openrouter.ai/api/v1",
    max_output_tokens: int = 2048,
    ledger: BudgetLedger | None = None,
    prices: PriceRegistry | None = None,
    max_input_tokens: int | None = None,
) -> RagasJudge:
    """Build an async Instructor-backed RAGAS judge without an API request."""
    role = settings.roles()["judge"]
    _validate_role("judge", role)
    if not api_key.strip():
        raise RunConfigurationError("OpenRouter judge API key is required")
    if not base_url.startswith("https://"):
        raise RunConfigurationError("Judge base URL must use HTTPS")
    if isinstance(max_output_tokens, bool) or max_output_tokens < 1:
        raise RunConfigurationError("max_output_tokens must be positive")
    try:
        ragas_version = version("ragas")
    except PackageNotFoundError as exc:
        raise RunConfigurationError("Install evaluation/ragas/requirements.txt in the evaluation environment") from exc
    if ragas_version != "0.4.3":
        raise RunConfigurationError(f"RAGAS 0.4.3 is required; found {ragas_version}")

    from evaluation.ragas.ragas_compat import install_vertexai_import_compatibility
    install_vertexai_import_compatibility()
    import instructor
    from openai import AsyncOpenAI
    from ragas.llms.adapters.instructor import InstructorLLM
    from ragas.llms.base import InstructorModelArgs

    guarded = any(value is not None for value in (ledger, prices, max_input_tokens))
    if guarded and (ledger is None or prices is None or max_input_tokens is None):
        raise RunConfigurationError("judge budget needs ledger, prices and input bound together")
    transport = None
    if guarded:
        import httpx
        from evaluation.program.budgeted_transport import BudgetedOpenRouterTransport

        if base_url.rstrip("/") != "https://openrouter.ai/api/v1":
            raise RunConfigurationError("budgeted judge requires the pinned OpenRouter endpoint")
        if settings.max_api_spend_usd is None or Decimal(str(settings.max_api_spend_usd)) != ledger.cap_usd:
            raise RunConfigurationError("judge ledger and settings must use the same USD cap")
        transport = BudgetedOpenRouterTransport(
            inner=httpx.AsyncHTTPTransport(), ledger=ledger, prices=prices,
            model_id=role.model_id, max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
        )
        http_client = httpx.AsyncClient(transport=transport)
        client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0,
                             http_client=http_client)
    else:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)
    structured = instructor.from_openai(client, mode=instructor.Mode.JSON)
    llm = InstructorLLM(
        client=structured, model=role.model_id, provider="openai",
        model_args=InstructorModelArgs(max_tokens=max_output_tokens),
    )
    if not llm.is_async:
        raise RunConfigurationError("RAGAS judge did not receive an async client")
    return RagasJudge(llm=llm, client=client, model_id=role.model_id,
                      budget_instrumented=guarded, transport=transport)
