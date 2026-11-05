from __future__ import annotations

import json
import pytest

from evaluation.program.config import EvaluationSettings, RunConfigurationError, configuration_hash, write_snapshot


def test_roles_are_distinct_and_snapshot_contains_no_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("CURRICULUM_TUTOR_GEMINI_API_KEY", "secret-gemini")
    monkeypatch.setenv("CURRICULUM_TUTOR_OPENROUTER_API_KEY", "secret-openrouter")
    settings = EvaluationSettings(_env_file=None, embedding_provider="gemini", embedding_model="embedding-x",
                                  relevance_embedding_provider="gemini", relevance_embedding_model="embedding-x",
                                  max_api_spend_usd=5)
    assert [r.provider for r in settings.roles().values()] == ["gemini", "openrouter", "openrouter"]
    settings.preflight(require_paid_calls=True)
    path = tmp_path / "config.json"
    digest = write_snapshot(path, settings)
    content = path.read_text(encoding="utf-8")
    assert "secret-gemini" not in content and "secret-openrouter" not in content
    assert digest == configuration_hash(json.loads(content)["configuration"])
    with pytest.raises(FileExistsError):
        write_snapshot(path, settings)


def test_unpinned_embeddings_and_budget_block_live_preflight():
    settings = EvaluationSettings(_env_file=None)
    with pytest.raises(RunConfigurationError, match="embedding model"):
        settings.preflight()
    settings = EvaluationSettings(_env_file=None, embedding_provider="gemini", embedding_model="x",
                                  relevance_embedding_provider="gemini", relevance_embedding_model="y")
    with pytest.raises(RunConfigurationError, match="MAX_API_SPEND"):
        settings.preflight(require_paid_calls=True)


def test_same_author_and_judge_model_is_not_independent():
    settings = EvaluationSettings(_env_file=None, author_model="same", judge_model="same")
    with pytest.raises(RunConfigurationError, match="differ"):
        settings.roles()


def test_invalid_depth_rejected():
    with pytest.raises(ValueError, match="final_k"):
        EvaluationSettings(_env_file=None, final_k=10, candidate_k=5)
