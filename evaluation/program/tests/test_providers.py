"""Offline checks for pinned model roles and disabled fallback."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from evaluation.program.config import EvaluationSettings, RunConfigurationError
from evaluation.program.providers import (
    LLMAdapters,
    ProviderRoutingError,
    build_role_provider,
    build_role_providers,
    build_reranker_provider,
    require_actual_identity,
    require_result_matches_role,
)


@dataclass
class FakeConfig:
    api_key: str
    model: str
    include_usage: bool = False
    max_output_tokens: int | None = None
    timeout_seconds: float | None = None


class FakeGemini:
    calls = 0

    def __init__(self, config):
        self.config = config

    def generate_messages(self, messages):
        type(self).calls += 1
        return "answer"


class FakeOpenRouter:
    calls = 0
    fallback_created = False

    def __init__(self, config, fallback=None):
        self.config = config
        if not fallback:
            type(self).fallback_created = True
        self.fallback = fallback or FakeGemini(FakeConfig(api_key="global-key", model="gemini-fallback"))

    def generate_messages(self, messages):
        type(self).calls += 1
        raise RuntimeError("simulated OpenRouter failure")


ADAPTERS = LLMAdapters(FakeConfig, FakeGemini, FakeConfig, FakeOpenRouter)
KEYS = {
    "CURRICULUM_TUTOR_GEMINI_API_KEY": "gemini-secret",
    "CURRICULUM_TUTOR_OPENROUTER_API_KEY": "openrouter-secret",
}


def settings():
    return EvaluationSettings(
        _env_file=None,
        answer_model="gemini-3.5-flash",
        author_model="moonshotai/kimi-k2.6",
        judge_model="deepseek/deepseek-v4.1-flash",
    )


def test_each_role_uses_its_pinned_provider_and_no_calls_at_build_time():
    FakeGemini.calls = FakeOpenRouter.calls = 0
    FakeOpenRouter.fallback_created = False
    providers = build_role_providers(settings(), environ=KEYS, adapters=ADAPTERS)
    assert providers["answer"].config == FakeConfig(KEYS["CURRICULUM_TUTOR_GEMINI_API_KEY"], "gemini-3.5-flash")
    assert providers["author"].config.model == "moonshotai/kimi-k2.6"
    assert providers["author"].config.include_usage
    assert providers["judge"].config.model == "deepseek/deepseek-v4.1-flash"
    assert providers["author"].fallback is providers["judge"].fallback is None
    assert not FakeOpenRouter.fallback_created
    assert FakeGemini.calls == FakeOpenRouter.calls == 0
    assert "secret" not in str(settings().public_snapshot())


def test_no_silent_fallback_on_openrouter_error():
    provider = build_role_provider(settings(), "judge", environ=KEYS, adapters=ADAPTERS)
    with pytest.raises(RuntimeError, match="simulated OpenRouter failure"):
        provider.generate_messages([])
    assert provider.fallback is None


def test_missing_role_credential_fails_before_loading_adapter():
    with pytest.raises(RunConfigurationError, match="CURRICULUM_TUTOR_OPENROUTER_API_KEY"):
        build_role_provider(settings(), "author", environ={}, adapters=ADAPTERS)


def test_wrong_model_family_fails_closed():
    wrong = EvaluationSettings(_env_file=None, author_model="openai/gpt-4.1-mini")
    with pytest.raises(RunConfigurationError, match="author role has an unexpected model family"):
        build_role_provider(wrong, "author", environ=KEYS, adapters=ADAPTERS)


def test_response_identity_requires_actual_matching_provider_and_model():
    role = settings().roles()["judge"]
    require_actual_identity(role, provider="openrouter", model_id=role.model_id)
    with pytest.raises(ProviderRoutingError, match="did not identify"):
        require_actual_identity(role, provider=None, model_id=None)
    with pytest.raises(ProviderRoutingError, match="unexpected provider/model"):
        require_actual_identity(role, provider="gemini", model_id=role.model_id)
    with pytest.raises(ProviderRoutingError, match="unexpected provider/model"):
        require_actual_identity(role, provider="openrouter", model_id="deepseek/other")


def test_unknown_role_rejected():
    with pytest.raises(ValueError, match="unknown evaluation role"):
        build_role_provider(settings(), "reranker", environ=KEYS, adapters=ADAPTERS)


def test_result_identity_accepts_gemini_resource_prefix_but_not_substitution():
    require_result_matches_role(settings(), "answer",
        SimpleNamespace(gateway="gemini", response_model="models/gemini-3.5-flash"))
    with pytest.raises(ProviderRoutingError, match="unexpected"):
        require_result_matches_role(settings(), "judge",
            SimpleNamespace(gateway="openrouter", response_model="deepseek/other"))


def test_reranker_requires_separate_model_pin_and_uses_injected_config():
    with pytest.raises(RunConfigurationError, match="RERANKER_MODEL"):
        build_reranker_provider(settings(), environ=KEYS)

    class FakeReranker:
        def __init__(self, config):
            self.config = config

    pinned = EvaluationSettings(_env_file=None, reranker_model="some/ranker-v1")
    provider = build_reranker_provider(pinned, environ=KEYS,
        config_type=FakeConfig, provider_type=FakeReranker)
    assert provider.config.model == "some/ranker-v1"
