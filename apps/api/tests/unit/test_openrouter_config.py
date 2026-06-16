from types import SimpleNamespace

from app.integrations.embeddings.base import OpenRouterEmbeddingConfig
from app.integrations.embeddings.base import OpenRouterEmbeddingProvider
from app.integrations.llm.base import OpenRouterLLMConfig
from app.integrations.llm.base import OpenRouterLLMProvider


def test_openrouter_embedding_config_from_env_uses_real_timeout() -> None:
    config = OpenRouterEmbeddingConfig.from_env()

    assert isinstance(config.timeout_seconds, float)
    assert config.timeout_seconds == 60.0


def test_openrouter_llm_config_from_env_uses_real_timeout() -> None:
    config = OpenRouterLLMConfig.from_env()

    assert isinstance(config.timeout_seconds, float)
    assert config.timeout_seconds == 60.0


def test_llm_provider_uses_fallback_when_openrouter_key_is_missing() -> None:
    fallback = SimpleNamespace(
        generate_messages=lambda messages: "gemini answer",
        stream_messages=lambda messages: iter(["gemini stream"]),
    )
    provider = OpenRouterLLMProvider(OpenRouterLLMConfig(api_key=None), fallback=fallback)

    assert provider.generate_messages([{"role": "user", "content": "hello"}]) == "gemini answer"
    assert provider.generate(system_prompt="system", user_prompt="hello", context="") == "gemini answer"
    assert list(provider.stream_messages([{"role": "user", "content": "hello"}])) == ["gemini stream"]


def test_embedding_provider_uses_fallback_when_openrouter_key_is_missing() -> None:
    fallback = SimpleNamespace(embed=lambda texts: [[1.0, 2.0, 3.0] for _ in texts])
    provider = OpenRouterEmbeddingProvider(OpenRouterEmbeddingConfig(api_key=None), fallback=fallback)

    assert provider.embed(["hello", "world"]) == [[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]]
