from __future__ import annotations

from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.integrations.llm import base


def test_openrouter_structured_result_and_string_compatibility(monkeypatch):
    calls = []

    def fake_request(url, payload, headers, timeout, **kwargs):
        calls.append(payload)
        return {"id": "r1", "model": "moonshotai/kimi-k2.6",
                "choices": [{"message": {"content": " answer "}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2}}

    monkeypatch.setattr(base, "_request_json", fake_request)
    provider = base.OpenRouterLLMProvider(
        base.OpenRouterLLMConfig(api_key="fake", model="moonshotai/kimi-k2.6", include_usage=True), fallback=object()
    )
    result = provider.generate_messages_with_result([{"role": "user", "content": "Q"}])
    assert result.text == "answer" and result.response_model == "moonshotai/kimi-k2.6"
    assert result.usage["prompt_tokens"] == 10
    assert provider.generate_messages([{"role": "user", "content": "Q"}]) == "answer"
    assert len(calls) == 2
    assert calls[0]["usage"] == {"include": True}


def test_gemini_structured_result_and_missing_metadata(monkeypatch):
    def fake_request(*args, **kwargs):
        return {"candidates": [{"content": {"parts": [{"text": "A"}]}}],
                "modelVersion": "gemini-3.5-flash", "responseId": "r2",
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1}}

    monkeypatch.setattr(base, "_request_json", fake_request)
    provider = base.GeminiLLMProvider(base.GeminiLLMConfig(api_key="fake", model="gemini-3.5-flash"))
    result = provider.generate_messages_with_result([{"role": "user", "content": "Q"}])
    assert result.text == "A" and result.response_model == "gemini-3.5-flash"
    assert result.usage["promptTokenCount"] == 3
    assert provider.generate_messages([{"role": "user", "content": "Q"}]) == "A"


def test_missing_response_model_is_not_invented(monkeypatch):
    monkeypatch.setattr(base, "_request_json", lambda *args, **kwargs:
                        {"choices": [{"message": {"content": "A"}}]})
    provider = base.OpenRouterLLMProvider(base.OpenRouterLLMConfig(api_key="fake"), fallback=object())
    assert provider.generate_messages_with_result([{"role": "user", "content": "Q"}]).response_model is None
