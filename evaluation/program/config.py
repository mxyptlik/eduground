"""Immutable, credential-free configuration for controlled evaluation runs."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RunConfigurationError(ValueError):
    pass


class ModelRole(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)
    provider: Literal["gemini", "openrouter"]
    model_id: str = Field(min_length=1)
    credential_env: str = Field(min_length=1)
    allow_fallback: bool = False


class EvaluationSettings(BaseSettings):
    """Only public model identifiers and settings; credentials stay in process env."""

    model_config = SettingsConfigDict(env_prefix="EDUGROUND_EVAL_", env_file=".env", extra="ignore")

    answer_model: str = "gemini-3.5-flash"
    answer_provider: Literal["gemini", "openrouter"] = "gemini"
    author_model: str = "moonshotai/kimi-k2.6"
    judge_model: str = "deepseek/deepseek-v4.1-flash"
    embedding_provider: Literal["gemini", "openrouter"] | None = None
    embedding_model: str | None = None
    relevance_embedding_provider: Literal["gemini", "openrouter"] | None = None
    relevance_embedding_model: str | None = None
    reranker_model: str | None = None
    reranker_max_output_tokens: int = Field(default=512, ge=1, le=4096)
    candidate_k: int = Field(default=20, ge=1, le=100)
    final_k: int = Field(default=5, ge=1, le=100)
    max_context_tokens: int = Field(default=8000, ge=128)
    max_api_spend_usd: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_depth(self) -> EvaluationSettings:
        if self.final_k > self.candidate_k:
            raise ValueError("final_k must be at most candidate_k")
        if bool(self.embedding_provider) != bool(self.embedding_model):
            raise ValueError("embedding provider and model must be configured together")
        if bool(self.relevance_embedding_provider) != bool(self.relevance_embedding_model):
            raise ValueError("relevancy embedding provider and model must be configured together")
        return self

    def roles(self) -> dict[str, ModelRole]:
        roles = {
            "answer": ModelRole(provider=self.answer_provider, model_id=self.answer_model,
                                credential_env=("CURRICULUM_TUTOR_GEMINI_API_KEY"
                                                if self.answer_provider == "gemini"
                                                else "CURRICULUM_TUTOR_OPENROUTER_API_KEY")),
            "author": ModelRole(provider="openrouter", model_id=self.author_model, credential_env="CURRICULUM_TUTOR_OPENROUTER_API_KEY"),
            "judge": ModelRole(provider="openrouter", model_id=self.judge_model, credential_env="CURRICULUM_TUTOR_OPENROUTER_API_KEY"),
        }
        if len({role.model_id for role in roles.values()}) != 3:
            raise RunConfigurationError("Author, answer and judge model IDs must differ")
        return roles

    def public_snapshot(self) -> dict:
        return {
            "roles": {name: {"provider": role.provider, "model_id": role.model_id, "allow_fallback": False}
                      for name, role in self.roles().items()},
            "embedding": {"provider": self.embedding_provider, "model_id": self.embedding_model},
            "relevance_embedding": {"provider": self.relevance_embedding_provider, "model_id": self.relevance_embedding_model},
            "reranker_model": self.reranker_model,
            "reranker_max_output_tokens": self.reranker_max_output_tokens,
            "candidate_k": self.candidate_k,
            "final_k": self.final_k,
            "max_context_tokens": self.max_context_tokens,
            "max_api_spend_usd": self.max_api_spend_usd,
        }

    def preflight(self, *, require_paid_calls: bool = False,
                  require_ingestion_embedding: bool = True,
                  require_relevance_embedding: bool = True) -> None:
        self.roles()
        if require_ingestion_embedding and not self.embedding_model:
            raise RunConfigurationError("Pin the ingestion/query embedding model before a run")
        if require_relevance_embedding and not self.relevance_embedding_model:
            raise RunConfigurationError("Pin the answer-relevancy embedding model before a run")
        if require_paid_calls and self.max_api_spend_usd is None:
            raise RunConfigurationError("Set EDUGROUND_EVAL_MAX_API_SPEND_USD before live calls")


def configuration_hash(snapshot: dict) -> str:
    return sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def write_snapshot(path: Path, settings: EvaluationSettings) -> str:
    """Write an exclusive run configuration file without credential values."""
    snapshot = settings.public_snapshot()
    digest = configuration_hash(snapshot)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump({"configuration_hash": digest, "configuration": snapshot}, output, indent=2)
        output.write("\n")
    return digest
