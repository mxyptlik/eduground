from app.integrations.embeddings.base import OpenRouterEmbeddingConfig
from app.integrations.llm.base import OpenRouterLLMConfig


def test_openrouter_embedding_config_from_env_uses_real_timeout() -> None:
    config = OpenRouterEmbeddingConfig.from_env()

    assert isinstance(config.timeout_seconds, float)
    assert config.timeout_seconds == 60.0


def test_openrouter_llm_config_from_env_uses_real_timeout() -> None:
    config = OpenRouterLLMConfig.from_env()

    assert isinstance(config.timeout_seconds, float)
    assert config.timeout_seconds == 60.0
