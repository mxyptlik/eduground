from datetime import datetime, timezone

import pytest

from evaluation.program.usage import usage_from_result


class Result:
    def __init__(self, gateway, model, usage):
        self.gateway = gateway
        self.response_model = model
        self.usage = usage


def test_openrouter_usage_preserves_reasoning_subset_and_missing_cache():
    result = Result("openrouter", "deepseek/deepseek-v4.1-flash", {
        "prompt_tokens": 100, "completion_tokens": 25,
        "completion_tokens_details": {"reasoning_tokens": 5},
    })
    record = usage_from_result(result, call_id="j1", operation="judge",
                               occurred_at=datetime(2026, 9, 30, tzinfo=timezone.utc))
    assert record.input_tokens == 100 and record.output_tokens == 25
    assert record.reasoning_output_tokens == 5
    assert record.cached_input_tokens is None


def test_openrouter_reasoning_outside_completion_tokens_is_summed():
    """Some upstreams report reasoning outside completion_tokens; both bill."""
    result = Result("openrouter", "moonshotai/kimi-k2.6", {
        "prompt_tokens": 3000, "completion_tokens": 200,
        "completion_tokens_details": {"reasoning_tokens": 4200},
    })
    record = usage_from_result(result, call_id="j2", operation="author")
    assert record.output_tokens == 4400
    assert record.reasoning_output_tokens == 4200


def test_gemini_usage_keeps_absent_counts_unknown():
    record = usage_from_result(Result("gemini", "models/gemini-3.5-flash",
                                      {"promptTokenCount": 12, "candidatesTokenCount": 4}),
                               call_id="a1", operation="answer")
    assert record.model == "gemini-3.5-flash"
    assert record.cached_input_tokens is None
    assert record.output_tokens is None
    assert record.cached_output_tokens == 0


def test_gemini_thoughts_are_added_once_to_output_total():
    record = usage_from_result(Result("gemini", "gemini-3.5-flash", {
        "promptTokenCount": 12, "cachedContentTokenCount": 0,
        "candidatesTokenCount": 4, "thoughtsTokenCount": 8,
    }), call_id="a2", operation="answer")
    assert record.output_tokens == 12
    assert record.reasoning_output_tokens == 8


def test_missing_actual_model_rejected():
    with pytest.raises(ValueError, match="identify"):
        usage_from_result(Result("openrouter", None, {}), call_id="x", operation="answer")
