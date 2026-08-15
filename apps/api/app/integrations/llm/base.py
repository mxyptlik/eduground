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
from app.core.logging import get_logger
from app.core.observability import traced_operation

logger = get_logger("app.llm")


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


class GeminiLLMProviderError(ProviderRequestError):
    def __init__(self, message: str, *, status_code: int = 502, retryable: bool = True, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="gemini_llm_error",
            message=message,
            status_code=status_code,
            retryable=retryable,
            provider="gemini",
            details=details or {},
        )


@dataclass(slots=True)
class OpenRouterLLMConfig:
    api_key: str | None = None
    model: str = "openai/gpt-4.1-mini"
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: float = 60.0
    max_output_tokens: int = 1536
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
            max_output_tokens=settings.openrouter_max_output_tokens,
            site_url=settings.openrouter_http_referer or settings.web_base_url,
            app_name=settings.openrouter_title or settings.app_name,
        )


@dataclass(slots=True)
class GeminiLLMConfig:
    api_key: str | None = None
    model: str = "gemini-2.0-flash"
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    timeout_seconds: float = 60.0
    max_output_tokens: int = 1536

    @classmethod
    def from_env(cls) -> "GeminiLLMConfig":
        defaults = cls()
        return cls(
            api_key=settings.gemini_api_key,
            model=settings.gemini_chat_model or defaults.model,
            base_url=settings.gemini_base_url or defaults.base_url,
            timeout_seconds=defaults.timeout_seconds,
            max_output_tokens=settings.gemini_max_output_tokens,
        )


def _request_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: float,
    *,
    provider: str = "openrouter",
    operation: str = "llm.generate",
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers=headers, method="POST")
    try:
        with traced_operation(
            f"provider.{provider}.llm.request",
            metric_name="eduground_provider_call",
            metric_labels={"provider": provider, "operation": operation},
        ):
            with request.urlopen(req, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
    except error.HTTPError as exc:  # pragma: no cover - exercised through integration tests
        detail = exc.read().decode("utf-8", errors="replace")
        error_type = GeminiLLMProviderError if provider == "gemini" else LLMProviderError
        provider_name = "Gemini" if provider == "gemini" else "OpenRouter"
        raise error_type(
            f"{provider_name} request failed with status {exc.code}: {detail}",
            status_code=502 if exc.code >= 500 else 424,
            retryable=exc.code >= 500 or exc.code == 429,
            details={"upstream_status_code": exc.code},
        ) from exc
    except error.URLError as exc:  # pragma: no cover - exercised through integration tests
        if provider == "gemini":
            raise GeminiLLMProviderError(f"Gemini request failed: {exc.reason}") from exc
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


def _gemini_model_resource(model: str) -> str:
    return model if model.startswith("models/") else f"models/{model}"


def _message_content_to_text(content: Any) -> str:
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
    return str(content) if content is not None else ""


def _to_gemini_payload(messages: list[dict[str, Any]], *, max_output_tokens: int | None = None) -> dict[str, Any]:
    system_parts: list[dict[str, str]] = []
    contents: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        text = _message_content_to_text(message.get("content"))
        if not text.strip():
            continue
        if role == "system":
            system_parts.append({"text": text})
            continue
        gemini_role = "model" if role == "assistant" else "user"
        contents.append({"role": gemini_role, "parts": [{"text": text}]})
    payload: dict[str, Any] = {"contents": contents or [{"role": "user", "parts": [{"text": ""}]}]}
    if system_parts:
        payload["systemInstruction"] = {"parts": system_parts}
    if max_output_tokens:
        payload["generationConfig"] = {"maxOutputTokens": max_output_tokens}
    return payload


def _extract_gemini_text(response: dict[str, Any]) -> str:
    candidates = response.get("candidates") or []
    if not candidates:
        raise GeminiLLMProviderError("Gemini response did not include any candidates")
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    text_parts = [part.get("text") for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str)]
    if text_parts:
        return "".join(text_parts)
    raise GeminiLLMProviderError("Gemini response did not include text content")


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
    def __init__(self, config: OpenRouterLLMConfig | None = None, fallback: LLMProvider | None = None) -> None:
        self.config = config or OpenRouterLLMConfig.from_env()
        self.fallback = fallback or (GeminiLLMProvider() if settings.gemini_fallback_enabled else None)

    def generate(self, *, system_prompt: str, user_prompt: str, context: str) -> str:
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
        try:
            if not self.config.api_key:
                raise DependencyUnavailableError(
                    code="openrouter_api_key_missing",
                    message="OpenRouter API key is not configured",
                    provider="openrouter",
                )
            response = _request_json(
                f"{self.config.base_url.rstrip('/')}/chat/completions",
                {"model": self.config.model, "messages": messages, "max_tokens": self.config.max_output_tokens},
                _headers_from_config(self.config),
                self.config.timeout_seconds,
            )
            return _extract_message_text(response).strip()
        except (DependencyUnavailableError, LLMProviderError) as exc:
            return self._generate_messages_with_fallback(messages, exc)

    def stream_messages(self, messages: list[dict[str, Any]]) -> Iterator[str]:
        try:
            yield from self._stream_openrouter_messages(messages)
        except (DependencyUnavailableError, LLMProviderError) as exc:
            yield from self._stream_messages_with_fallback(messages, exc)

    def _stream_openrouter_messages(self, messages: list[dict[str, Any]]) -> Iterator[str]:
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
            try:
                with httpx.stream(
                    "POST",
                    f"{self.config.base_url.rstrip('/')}/chat/completions",
                    headers=_headers_from_config(self.config),
                    json={
                        "model": self.config.model,
                        "messages": messages,
                        "stream": True,
                        "max_tokens": self.config.max_output_tokens,
                    },
                    timeout=self.config.timeout_seconds,
                ) as response:
                    if response.status_code >= 400:
                        error_body = response.read().decode("utf-8", errors="replace")
                        raise LLMProviderError(
                            f"OpenRouter request failed with status {response.status_code}: {error_body}",
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
            except httpx.HTTPError as exc:
                raise LLMProviderError(f"OpenRouter stream request failed: {exc}") from exc

    def _generate_messages_with_fallback(self, messages: list[dict[str, Any]], original_error: Exception) -> str:
        if self.fallback is None:
            raise original_error
        try:
            logger.warning(
                "OpenRouter LLM failed; falling back to Gemini",
                extra={"extra_json": {"error": str(original_error), "fallback_provider": "gemini"}},
            )
            return self.fallback.generate_messages(messages).strip()
        except DependencyUnavailableError:
            raise original_error

    def _stream_messages_with_fallback(self, messages: list[dict[str, Any]], original_error: Exception) -> Iterator[str]:
        if self.fallback is None:
            raise original_error
        try:
            logger.warning(
                "OpenRouter LLM stream failed; falling back to Gemini",
                extra={"extra_json": {"error": str(original_error), "fallback_provider": "gemini"}},
            )
            yield from self.fallback.stream_messages(messages)
        except DependencyUnavailableError:
            raise original_error


class GeminiLLMProvider(LLMProvider):
    def __init__(self, config: GeminiLLMConfig | None = None) -> None:
        self.config = config or GeminiLLMConfig.from_env()

    def generate(self, *, system_prompt: str, user_prompt: str, context: str) -> str:
        message_content = user_prompt if not context.strip() else f"Context:\n{context}\n\nQuestion:\n{user_prompt}"
        return self.generate_messages(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message_content},
            ]
        )

    def generate_messages(self, messages: list[dict[str, Any]]) -> str:
        if not self.config.api_key:
            raise DependencyUnavailableError(
                code="gemini_api_key_missing",
                message="Gemini API key is not configured",
                provider="gemini",
            )
        response = _request_json(
            f"{self.config.base_url.rstrip('/')}/{_gemini_model_resource(self.config.model)}:generateContent",
            _to_gemini_payload(messages, max_output_tokens=self.config.max_output_tokens),
            {
                "x-goog-api-key": self.config.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            self.config.timeout_seconds,
            provider="gemini",
        )
        return _extract_gemini_text(response).strip()

    def stream_messages(self, messages: list[dict[str, Any]]) -> Iterator[str]:
        yield self.generate_messages(messages)


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
