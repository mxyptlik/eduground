"""Explicit, offline construction of the three evaluation LLM roles.

The application adapters are imported only when a real provider is built.
Constructing a provider performs no network request; capability probes and
actual calls belong to later, budgeted evaluation tasks.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
import os
from pathlib import Path
import sys
from typing import Any, Literal, Mapping

from evaluation.program.config import EvaluationSettings, ModelRole, RunConfigurationError

RoleName = Literal["answer", "author", "judge"]


class ProviderRoutingError(RunConfigurationError):
    """A provider used a different model than the frozen run requested."""


@dataclass(frozen=True)
class LLMAdapters:
    """Injected constructors; keeping them here makes selection testable offline."""

    gemini_config: type
    gemini_provider: type
    openrouter_config: type
    openrouter_provider: type


def load_app_llm_adapters() -> LLMAdapters:
    """Load Eduground's existing adapters lazily from the repository checkout."""
    api_root = Path(__file__).resolve().parents[2] / "apps" / "api"
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))
    llm = import_module("app.integrations.llm.base")
    return LLMAdapters(
        gemini_config=llm.GeminiLLMConfig,
        gemini_provider=llm.GeminiLLMProvider,
        openrouter_config=llm.OpenRouterLLMConfig,
        openrouter_provider=llm.OpenRouterLLMProvider,
    )


def build_role_provider(
    settings: EvaluationSettings,
    role_name: RoleName,
    *,
    environ: Mapping[str, str] | None = None,
    adapters: LLMAdapters | None = None,
    reasoning: dict | None = None,
) -> Any:
    """Build one pinned-role adapter without a call or a model fallback.

    Credentials are read only from ``environ`` (process environment by
    default), never copied into run snapshots. The returned adapter exposes
    Eduground's existing generate/generate_messages methods.
    """
    if role_name not in ("answer", "author", "judge"):
        raise ValueError(f"unknown evaluation role: {role_name}")
    role = settings.roles()[role_name]
    _validate_role(role_name, role)
    source = os.environ if environ is None else environ
    key = source.get(role.credential_env, "").strip()
    if not key:
        raise RunConfigurationError(f"{role.credential_env} is required for the {role_name} role")
    constructors = adapters if adapters is not None else load_app_llm_adapters()

    if role_name == "answer" and role.provider == "gemini":
        config = constructors.gemini_config(api_key=key, model=role.model_id)
        provider = constructors.gemini_provider(config=config)
    else:
        config_kwargs: dict = {
            "api_key": key, "model": role.model_id, "include_usage": True,
            # Reasoning-capable pinned models (Kimi K2.6, Qwen3.7, DeepSeek V4.1)
            # spend the output budget on reasoning first; the 1536 API-wide
            # default left responses with empty content. All generation roles
            # get the same 32k headroom.
            "max_output_tokens": {"answer": 65536, "author": 32768, "judge": 32768}[role_name],
            # Generous read timeout: heavy grounded answers over large local
            # contexts can stall far past the 60-second default, and a single
            # timeout poisons the run's ledger for every later step.
            "timeout_seconds": {"answer": 240.0, "author": 300.0, "judge": 180.0}[role_name],
        }
        if reasoning is not None:
            config_kwargs["reasoning"] = dict(reasoning)
        config = constructors.openrouter_config(**config_kwargs)
        # OpenRouterLLMProvider uses ``fallback or app_default`` in its
        # constructor. A truthy sentinel prevents it from creating a Gemini
        # fallback even if the application-wide setting enables one.
        provider = constructors.openrouter_provider(config=config, fallback=_NO_FALLBACK_SENTINEL)
        provider.fallback = None

    if provider.config.model != role.model_id:
        raise ProviderRoutingError(f"{role_name} adapter changed the requested model")
    if role.provider == "openrouter" and getattr(provider, "fallback", None) is not None:
        raise ProviderRoutingError(f"{role_name} adapter retained a fallback")
    return provider


def build_role_providers(
    settings: EvaluationSettings,
    *,
    environ: Mapping[str, str] | None = None,
    adapters: LLMAdapters | None = None,
) -> dict[RoleName, Any]:
    """Build answer, author, and judge adapters with independent role IDs."""
    return {
        name: build_role_provider(settings, name, environ=environ, adapters=adapters)
        for name in ("answer", "author", "judge")
    }


def build_reranker_provider(
    settings: EvaluationSettings, *, environ: Mapping[str, str] | None = None,
    config_type: type | None = None, provider_type: type | None = None,
) -> Any:
    """Build a separately pinned OpenRouter reranker without a network call."""
    if not settings.reranker_model:
        raise RunConfigurationError("Set EDUGROUND_EVAL_RERANKER_MODEL for rerank runs")
    source = os.environ if environ is None else environ
    key = source.get("CURRICULUM_TUTOR_OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RunConfigurationError("CURRICULUM_TUTOR_OPENROUTER_API_KEY is required for the reranker")
    if config_type is None or provider_type is None:
        api_root = Path(__file__).resolve().parents[2] / "apps" / "api"
        if str(api_root) not in sys.path:
            sys.path.insert(0, str(api_root))
        module = import_module("app.integrations.rerank.openrouter_adapter")
        config_type = module.OpenRouterRerankerConfig
        provider_type = module.OpenRouterRerankerProvider
    config = config_type(api_key=key, model=settings.reranker_model, include_usage=True,
                         max_output_tokens=settings.reranker_max_output_tokens)
    provider = provider_type(config=config)
    if provider.config.model != settings.reranker_model:
        raise ProviderRoutingError("reranker adapter changed the requested model")
    return provider


def require_actual_identity(role: ModelRole, *, provider: str | None, model_id: str | None) -> None:
    """Fail a controlled run if response routing cannot be verified exactly.

    Evaluation callers use the optional structured adapter result to supply
    returned provider/model metadata. A requested model alone is insufficient.
    """
    if provider is None or model_id is None:
        raise ProviderRoutingError("provider response did not identify its actual model")
    if (provider, model_id) != (role.provider, role.model_id):
        raise ProviderRoutingError(
            f"unexpected provider/model: expected {role.provider}/{role.model_id}, got {provider}/{model_id}"
        )


def require_result_matches_role(settings: EvaluationSettings, role_name: RoleName, result: Any) -> None:
    """Check the *returned* model, not only the model requested in config."""
    role = settings.roles()[role_name]
    gateway = getattr(result, "gateway", None)
    model_id = getattr(result, "response_model", None)
    if gateway == "gemini" and isinstance(model_id, str):
        model_id = model_id.removeprefix("models/")
    require_actual_identity(role, provider=gateway, model_id=model_id)


def _validate_role(name: RoleName, role: ModelRole) -> None:
    if role.allow_fallback:
        raise RunConfigurationError(f"{name} role cannot enable fallback in a controlled run")
    if role.provider not in (("gemini", "openrouter") if name == "answer" else ("openrouter",)):
        expected = "gemini or openrouter" if name == "answer" else "openrouter"
        raise RunConfigurationError(f"{name} role must use {expected}")
    if name == "answer" and role.provider == "openrouter":
        lowered = role.model_id.lower()
        if lowered.startswith("moonshotai/kimi") or lowered.startswith("deepseek/"):
            raise RunConfigurationError("answer role must not reuse the author or judge model")
        return
    family = {"answer": ("gemini-", "models/gemini-"), "author": ("moonshotai/kimi",), "judge": ("deepseek/",)}[name]
    if not role.model_id.lower().startswith(family):
        raise RunConfigurationError(f"{name} role has an unexpected model family: {role.model_id}")


_NO_FALLBACK_SENTINEL = object()
