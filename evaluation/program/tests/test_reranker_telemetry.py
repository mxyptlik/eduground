import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.integrations.rerank import openrouter_adapter as module


class Response:
    status_code = 200
    text = ""

    def __init__(self, ranks):
        self.ranks = ranks

    def json(self):
        return {"id": "r1", "model": "reranker/model", "usage": {"prompt_tokens": 10},
                "choices": [{"message": {"content": json.dumps({"ranked_indices": self.ranks})}}]}


def _provider(monkeypatch, ranks):
    class Client:
        def __init__(self, timeout):
            self.timeout = timeout

        def post(self, url, headers, json):
            assert json["model"] == "reranker/model"
            assert headers["Authorization"] == "Bearer fixture-key"
            assert json.get("usage") == {"include": True}
            return Response(ranks)

    monkeypatch.setattr(module.httpx, "Client", Client)
    config = module.OpenRouterRerankerConfig(api_key="fixture-key", model="reranker/model", max_passage_chars=4,
                                             include_usage=True)
    return module.OpenRouterRerankerProvider(config)


def _items():
    return [{"chunk": SimpleNamespace(id="a", text="aaaaaa"), "source": SimpleNamespace(id="s1")},
            {"chunk": SimpleNamespace(id="b", text="bbbbbb"), "source": SimpleNamespace(id="s2")}]


def test_reranker_config_is_injected_and_response_usage_preserved(monkeypatch):
    provider = _provider(monkeypatch, [{"index": 1, "score": 0.9}, {"index": 0, "score": 0.3}])
    result = provider.rerank_with_result("Q", _items(), strict=True)
    assert [item["chunk"].id for item in result.items] == ["b", "a"]
    assert result.response_model == "reranker/model"
    assert result.usage["prompt_tokens"] == 10
    assert result.truncated_passage_count == 2
    assert result.ranked_coverage == 1


def test_strict_mode_rejects_partial_model_ranking(monkeypatch):
    provider = _provider(monkeypatch, [{"index": 1, "score": 0.9}])
    with pytest.raises(module.RerankerProviderError, match="omitted"):
        provider.rerank_with_result("Q", _items(), strict=True)
    result = provider.rerank_with_result("Q", _items())
    assert result.ranked_coverage == 0.5
    assert len(result.items) == 2
