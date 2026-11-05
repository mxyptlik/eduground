import asyncio
from types import SimpleNamespace

import pytest

from evaluation.program.metrics import ragas as metric_module


def test_no_context_is_na_without_judge_call(monkeypatch):
    monkeypatch.setattr(metric_module, "_metric_class", lambda name: (_ for _ in ()).throw(AssertionError("judge called")))
    result = asyncio.run(metric_module.score_ragas_metric("faithfulness", llm=object(),
        user_input="What happened?", response="Something happened.", retrieved_contexts=[]))
    assert result.status == "not_applicable" and result.value is None


def test_reference_and_metric_inputs_sent_only_to_selected_metric(monkeypatch):
    seen = []

    class Metric:
        def __init__(self, **kwargs):
            seen.append(("init", kwargs))

        async def ascore(self, **kwargs):
            seen.append(("score", kwargs))
            return SimpleNamespace(value=0.75)

    monkeypatch.setattr(metric_module, "_metric_class", lambda name: Metric)
    result = asyncio.run(metric_module.score_ragas_metric("context_precision", llm="judge",
        user_input="What happened?", response="A", reference="B", retrieved_contexts=["C"]))
    assert result.value == 0.75
    assert seen[-1] == ("score", {"user_input": "What happened?", "reference": "B", "retrieved_contexts": ["C"]})


def test_invalid_ragas_value_is_failure_not_zero(monkeypatch):
    class Metric:
        def __init__(self, **kwargs):
            pass

        async def ascore(self, **kwargs):
            return SimpleNamespace(value=float("nan"))

    monkeypatch.setattr(metric_module, "_metric_class", lambda name: Metric)
    with pytest.raises(ValueError, match="out-of-range"):
        asyncio.run(metric_module.score_ragas_metric("factual_correctness_f1", llm="judge",
            user_input="What happened?", response="A", reference="B"))


def test_relevancy_requires_embeddings():
    with pytest.raises(ValueError, match="embeddings"):
        asyncio.run(metric_module.score_ragas_metric("answer_relevancy", llm="judge",
            user_input="What happened?", response="A"))
