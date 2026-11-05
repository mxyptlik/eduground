"""NaN and out-of-range reranker scores are dropped, never stored."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.integrations.rerank.openrouter_adapter import OpenRouterRerankerConfig, OpenRouterRerankerProvider


class FakeResponse:
    def __init__(self, body: str):
        self.status_code = 200
        self._body = body

    def json(self):
        return json.loads(self._body)


class FakeClient:
    def __init__(self, body: str):
        self.body = body

    def post(self, url, *, headers, json):
        return FakeResponse(self.body)


def _items():
    return [
        {"chunk": SimpleNamespace(id="c1", text="alpha"), "source": SimpleNamespace(id="s1"),
         "score": 0.9, "rank_before": 1, "rank_after": 1, "rerank_score": None},
        {"chunk": SimpleNamespace(id="c2", text="beta"), "source": SimpleNamespace(id="s1"),
         "score": 0.5, "rank_before": 2, "rank_after": 2, "rerank_score": None},
    ]


def _body(inner: str) -> str:
    # The adapter reads the model's JSON text from choices[0].message.content;
    # NaN stays inside that text because Python's json.loads accepts it.
    return json.dumps({"model": "pinned/model", "choices": [{"message": {"content": inner}}]})


def test_non_strict_rerank_drops_nan_and_out_of_range_scores():
    body = _body('{"ranked_indices": [{"index": 0, "score": NaN}, {"index": 1, "score": 7}]}')
    provider = OpenRouterRerankerProvider(
        config=OpenRouterRerankerConfig(api_key="k", model="pinned/model"), client=FakeClient(body))
    output = provider.rerank("query", _items())
    scores = {item["chunk"].id: item["rerank_score"] for item in output}
    assert scores["c1"] is None and scores["c2"] is None
    assert all(item["rerank_score"] is None or item["rerank_score"] == item["rerank_score"]  # not NaN
               for item in output)


def test_strict_rerank_still_rejects_non_finite_scores():
    import pytest

    from app.integrations.rerank.openrouter_adapter import RerankerProviderError

    body = _body('{"ranked_indices": [{"index": 0, "score": NaN}, {"index": 1, "score": 0.5}]}')
    provider = OpenRouterRerankerProvider(
        config=OpenRouterRerankerConfig(api_key="k", model="pinned/model"), client=FakeClient(body))
    with pytest.raises(RerankerProviderError, match="invalid score"):
        provider.rerank_with_result("query", _items(), strict=True)
