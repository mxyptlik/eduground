from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
import json
from urllib import error, request

import httpx

from app.core.config import settings
from app.core.errors import DependencyUnavailableError, ProviderRequestError
from app.core.observability import traced_operation


class LLMProviderError(ProviderRequestError):
    def __init__(self, message: str, *, status_code: int = 502, retryable: bool = True, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="openrouter_llm_error",
            message=message,
            status_code=status_code,
            retryable=retryable,
            provider="openrouter",
            details=details or {},
        )


@dataclass(slots=True)
class OpenRouterLLMConfig:
    api_key: str | None = None
    model: str = "openai/gpt-4.1-mini"
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: float = 60.0
    site_url: str | None = None
    app_name: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "OpenRouterLLMConfig":
        defaults = cls()
        return cls(
            api_key=settings.openrouter_api_key,
            model=settings.openrouter_chat_model or defaults.model,
            base_url=settings.openrouter_base_url or defaults.base_url,
            timeout_seconds=defaults.timeout_seconds,
            site_url=settings.openrouter_http_referer or settings.web_base_url,
            app_name=settings.openrouter_title or settings.app_name,
        )


def _request_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout_seconds: float) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers=headers, method="POST")
    try:
        with traced_operation(
            "provider.openrouter.llm.request",
            metric_name="eduground_provider_call",
            metric_labels={"provider": "openrouter", "operation": "llm.generate"},
        ):
            with request.urlopen(req, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
    except error.HTTPError as exc:  # pragma: no cover - exercised through integration tests
        detail = exc.read().decode("utf-8", errors="replace")
        raise LLMProviderError(
            f"OpenRouter request failed with status {exc.code}: {detail}",
            status_code=502 if exc.code >= 500 else 424,
            retryable=exc.code >= 500 or exc.code == 429,
            details={"upstream_status_code": exc.code},
        ) from exc
    except error.URLError as exc:  # pragma: no cover - exercised through integration tests
        raise LLMProviderError(f"OpenRouter request failed: {exc.reason}") from exc
    return json.loads(raw) if raw else {}


def _headers_from_config(config: OpenRouterLLMConfig) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {config.api_key}" if config.api_key else "",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if config.site_url:
        headers["HTTP-Referer"] = config.site_url
    if config.app_name:
        headers["X-Title"] = config.app_name
    headers.update(config.extra_headers)
    return {key: value for key, value in headers.items() if value}


def _extract_message_text(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        raise LLMProviderError("OpenRouter response did not include any choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "".join(parts)
    raise LLMProviderError("OpenRouter response did not include message content")


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, *, system_prompt: str, user_prompt: str, context: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def generate_messages(self, messages: list[dict[str, Any]]) -> str:
        raise NotImplementedError

    @abstractmethod
    def stream_messages(self, messages: list[dict[str, Any]]) -> Iterator[str]:
        raise NotImplementedError


class OpenRouterLLMProvider(LLMProvider):
    def __init__(self, config: OpenRouterLLMConfig | None = None) -> None:
        self.config = config or OpenRouterLLMConfig.from_env()

    def generate(self, *, system_prompt: str, user_prompt: str, context: str) -> str:
        if not self.config.api_key:
            raise DependencyUnavailableError(
                code="openrouter_api_key_missing",
                message="OpenRouter API key is not configured",
                provider="openrouter",
            )
        message_content = user_prompt if not context.strip() else f"Context:\n{context}\n\nQuestion:\n{user_prompt}"
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message_content},
            ],
        }
        return self.generate_messages(payload["messages"])

    def generate_messages(self, messages: list[dict[str, Any]]) -> str:
        response = _request_json(
            f"{self.config.base_url.rstrip('/')}/chat/completions",
            {"model": self.config.model, "messages": messages},
            _headers_from_config(self.config),
            self.config.timeout_seconds,
        )
        return _extract_message_text(response).strip()

    def stream_messages(self, messages: list[dict[str, Any]]) -> Iterator[str]:
        if not self.config.api_key:
            raise DependencyUnavailableError(
                code="openrouter_api_key_missing",
                message="OpenRouter API key is not configured",
                provider="openrouter",
            )

        with traced_operation(
            "provider.openrouter.llm.stream",
            metric_name="eduground_provider_call",
            metric_labels={"provider": "openrouter", "operation": "llm.stream"},
        ):
            with httpx.stream(
                "POST",
                f"{self.config.base_url.rstrip('/')}/chat/completions",
                headers=_headers_from_config(self.config),
                json={"model": self.config.model, "messages": messages, "stream": True},
                timeout=self.config.timeout_seconds,
            ) as response:
                if response.status_code >= 400:
                    raise LLMProviderError(
                        f"OpenRouter request failed with status {response.status_code}: {response.text}",
                        status_code=502 if response.status_code >= 500 else 424,
                        retryable=response.status_code >= 500 or response.status_code == 429,
                        details={"upstream_status_code": response.status_code},
                    )
                for line in response.iter_lines():
                    if not line:
                        continue
                    text = line.decode("utf-8") if isinstance(line, bytes) else line
                    if not text.startswith("data:"):
                        continue
                    payload = text[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    delta = _extract_stream_delta(chunk)
                    if delta:
                        yield delta


def _extract_stream_delta(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""
