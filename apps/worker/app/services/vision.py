"""Provider-agnostic, ledger-guarded visual understanding (Layer 3).

Image in, strict JSON text out. OpenRouter chat-completions and Gemini
generateContent are both supported; the provider/model pair is pinned and the
returned identity and usage are verified through the shared budget ledger.
Identical images (SHA-256) are described once and reused from a library cache.
Optional batching sends several uncached figures in one request (same tokens,
fewer round trips); per-figure cache entries are still written individually so
a later run only pays for genuinely new figures.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib import error, request
from uuid import uuid4

from app.ledger import VISUAL_UNDERSTANDING_OPERATION, LedgerGuard
from app.services.model_output import ModelOutputError, validate_model_json
from app.settings import settings


VISION_PROMPT_VERSION = "v2"

_SYSTEM = (
    "You transcribe visual content from university course material for a retrieval index. "
    "Rules: transcribe every visible label and word exactly as shown; never invent, translate or guess text; "
    "describe only what is visible and the relationships it depicts; put anything illegible or ambiguous in the "
    "uncertainty list; return only the JSON the user's schema requests, with no commentary."
)

_USER_PROMPT = (
    "Describe this figure from course material. Return strict JSON only in exactly this shape: "
    '{"figure_type": "diagram|chart|table|screenshot|photo", '
    '"verbatim_text": "every visible label/word, exactly as shown (use \\"\\" only if truly none)", '
    '"table_markdown": "the table as markdown or an empty string", '
    '"description": "what the figure shows and the relationships it depicts", '
    '"relationships": ["short relation statements"], '
    '"uncertainty": ["anything illegible or ambiguous"]}. '
    "All keys are required. Do not speculate beyond what is visible. Do not invent labels."
)

_BATCH_PROMPT = (
    "Describe each of the {count} figures from course material, in order. Return strict JSON only in exactly this "
    'shape: {{"figures": [{{"figure_type": "diagram|chart|table|screenshot|photo", '
    '"verbatim_text": "every visible label/word, exactly as shown (use \\"\\" only if truly none)", '
    '"table_markdown": "the table as markdown or an empty string", '
    '"description": "what the figure shows and the relationships it depicts", '
    '"relationships": ["short relation statements"], '
    '"uncertainty": ["anything illegible or ambiguous"]}}]}}. '
    "The figures array must contain exactly {count} entries, in the same order as the images. "
    "Do not speculate beyond what is visible. Do not invent labels."
)

_FIGURE_SCHEMA = {
    "type": "object",
    "properties": {
        "figure_type": {"type": "string", "enum": ["diagram", "chart", "table", "screenshot", "photo"]},
        "verbatim_text": {"type": "string"},
        "table_markdown": {"type": "string"},
        "description": {"type": "string"},
        "relationships": {"type": "array", "items": {"type": "string"}},
        "uncertainty": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["figure_type", "verbatim_text", "description", "relationships", "uncertainty"],
}
_BATCH_SCHEMA = {
    "type": "object",
    "properties": {"figures": {"type": "array", "items": _FIGURE_SCHEMA}},
    "required": ["figures"],
}


class VisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class VisionUnderstanding:
    figure_type: str
    verbatim_text: str
    table_markdown: str | None
    description: str
    relationships: tuple[str, ...]
    uncertainty: tuple[str, ...]
    described_by: str | None = None


def parse_vision_object(payload: Any) -> VisionUnderstanding:
    """Validate one figure object; unknown keys are ignored, missing keys fail."""
    if not isinstance(payload, dict):
        raise VisionError("vision response must be a JSON object")

    def _text(key: str, *aliases: str) -> str:
        for candidate in (key, *aliases):
            item = payload.get(candidate)
            if isinstance(item, str) and item.strip():
                return " ".join(item.split())
            if (isinstance(item, list) and item
                    and all(isinstance(entry, str) for entry in item) and any(entry.strip() for entry in item)):
                return " ".join(" ".join(entry.split()) for entry in item if entry.strip())
        raise VisionError(f"vision response is missing {key}")

    table = payload.get("table_markdown")
    if table is not None and not isinstance(table, str):
        raise VisionError("table_markdown must be a string or null")

    def _list(key: str, *aliases: str) -> tuple[str, ...]:
        items = None
        for candidate in (key, *aliases):
            if candidate in payload:
                items = payload[candidate]
                break
        if items is None:
            return ()
        if isinstance(items, str):
            items = [items] if items.strip() else []
        if not isinstance(items, list) or any(not isinstance(entry, str) for entry in items):
            raise VisionError(f"{key} must be a list of strings")
        return tuple(" ".join(entry.split()) for entry in items if entry.strip())

    figure_type = _text("figure_type").lower()
    aliases = {"figure": "diagram", "illustration": "diagram", "graph": "chart", "image": "photo"}
    figure_type = aliases.get(figure_type, figure_type)
    if figure_type not in {"diagram", "chart", "table", "screenshot", "photo"}:
        raise VisionError(f"unsupported figure_type: {figure_type}")
    return VisionUnderstanding(
        figure_type=figure_type,
        verbatim_text=_text("verbatim_text", "visible_text", "text"),
        table_markdown=" ".join(table.split()) if isinstance(table, str) and table.strip() else None,
        description=_text("description", "summary"),
        relationships=_list("relationships", "relations"),
        uncertainty=_list("uncertainty", "uncertainties"),
    )


def parse_vision_json(value: str) -> VisionUnderstanding:
    """Strict parse of a single-figure response through the shared validator."""
    try:
        payload = validate_model_json(value, label="vision response", expect="object")
    except ModelOutputError as exc:
        raise VisionError(str(exc)) from exc
    return parse_vision_object(payload)


def parse_vision_batch_json(value: str, *, expected_count: int) -> list[VisionUnderstanding]:
    """Strict parse of a batched response; the count must match exactly."""
    try:
        payload = validate_model_json(value, label="vision batch response", expect="object",
                                      required_keys=("figures",))
    except ModelOutputError as exc:
        raise VisionError(str(exc)) from exc
    figures = payload.get("figures")
    if not isinstance(figures, list):
        raise VisionError("vision batch response is missing the figures array")
    if len(figures) != expected_count:
        raise VisionError(f"vision batch returned {len(figures)} figures, expected {expected_count}")
    return [parse_vision_object(entry) for entry in figures]


ImagePart = tuple[bytes, str]


class VisionClient:
    """One pinned provider/model; every provider attempt goes through the guardian."""

    def __init__(
        self, *, provider: str, model: str, api_key: str, guard: LedgerGuard,
        cache_dir: str | Path = ".eduground_visual_cache", max_output_tokens: int = 1024,
        batch_size: int = 1, base_url: str | None = None, referer: str | None = None,
        title: str | None = None, timeout_seconds: float = 60.0, opener=None,
    ) -> None:
        if provider not in {"openrouter", "gemini"}:
            raise VisionError(f"unsupported vision provider: {provider}")
        if not api_key.strip() or not model.strip():
            raise VisionError("vision provider requires an API key and a pinned model")
        if isinstance(batch_size, bool) or not 1 <= batch_size <= 8:
            raise VisionError("batch_size must be between 1 and 8")
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.guard = guard
        self.cache_dir = Path(cache_dir)
        self.max_output_tokens = max_output_tokens
        self.batch_size = batch_size
        self.base_url = (base_url or ("https://openrouter.ai/api/v1" if provider == "openrouter"
                                      else settings.gemini_base_url)).rstrip("/")
        self.referer = referer
        self.title = title
        self.timeout_seconds = timeout_seconds
        self._opener = opener or request.urlopen

    def cache_key(self, image_bytes: bytes) -> str:
        material = sha256(image_bytes).hexdigest()
        return sha256(f"{material}|{VISION_PROMPT_VERSION}|{self.provider}|{self.model}".encode("utf-8")).hexdigest()

    def describe(self, image_bytes: bytes, *, mime: str, context: str = "") -> VisionUnderstanding:
        if not image_bytes:
            raise VisionError("figure image is empty")
        key = self.cache_key(image_bytes)
        cached = self._load_cache(key)
        if cached is not None:
            return cached
        raw = self._dispatch([(image_bytes, mime)], context=context, batched=False)
        understanding = parse_vision_json(self._response_text(raw))
        understanding = replace(understanding, described_by=f"{self.provider}/{self.model}")
        self._store_cache(key, understanding)
        return understanding

    def describe_many(self, items: Sequence[Mapping[str, Any]]) -> list[VisionUnderstanding | VisionError]:
        """Describe several figures; cached ones are free, the rest may be batched.

        Returns results aligned with ``items``; a failed batch yields a
        ``VisionError`` for each figure in that batch instead of raising.
        """
        results: list[VisionUnderstanding | VisionError | None] = [None] * len(items)
        pending: list[tuple[int, Mapping[str, Any]]] = []
        for index, item in enumerate(items):
            image = item.get("image")
            if not isinstance(image, bytes) or not image:
                results[index] = VisionError("figure image is empty")
                continue
            cached = self._load_cache(self.cache_key(image))
            if cached is not None:
                results[index] = cached
            else:
                pending.append((index, item))
        for start in range(0, len(pending), self.batch_size):
            chunk = pending[start:start + self.batch_size]
            if len(chunk) == 1 or self.batch_size == 1:
                outcomes: list[VisionUnderstanding | VisionError]
                index, item = chunk[0]
                try:
                    outcome = self.describe(item["image"], mime=str(item.get("mime") or "image/png"),
                                            context=str(item.get("context") or ""))
                except Exception as exc:  # noqa: BLE001 - reported per figure
                    outcome = exc if isinstance(exc, VisionError) else VisionError(str(exc))
                outcomes = [outcome]
            else:
                try:
                    outcomes = self._describe_batch([item for _, item in chunk])
                except Exception as exc:  # noqa: BLE001 - the whole batch failed
                    failure = exc if isinstance(exc, VisionError) else VisionError(str(exc))
                    outcomes = [failure] * len(chunk)
            for (index, item), outcome in zip(chunk, outcomes):
                if isinstance(outcome, VisionError):
                    results[index] = outcome
                else:
                    self._store_cache(self.cache_key(item["image"]), outcome)
                    results[index] = outcome
        return [result if isinstance(result, VisionError) else result for result in results]  # type: ignore[return-value]

    def _describe_batch(self, items: Sequence[Mapping[str, Any]]) -> list[VisionUnderstanding]:
        images: list[ImagePart] = [(item["image"], str(item.get("mime") or "image/png")) for item in items]
        context = " | ".join(str(item.get("context") or "") for item in items)[:600]
        raw = self._dispatch(images, context=context, batched=True)
        results = parse_vision_batch_json(self._response_text(raw), expected_count=len(images))
        return [replace(understanding, described_by=f"{self.provider}/{self.model}")
                for understanding in results]

    def _dispatch(self, images: Sequence[ImagePart], *, context: str, batched: bool) -> Any:
        template = _BATCH_PROMPT if batched else _USER_PROMPT
        prompt = template.format(count=len(images)) if batched else template
        if context.strip():
            prompt = f"{prompt}\n\nSlide context: {context[:400]}"
        if self.provider == "openrouter":
            return self._dispatch_openrouter(images, prompt=prompt)
        return self._dispatch_gemini(images, prompt=prompt, batched=batched)

    def _bounds(self, images: Sequence[ImagePart], prompt: str) -> dict[str, int]:
        image_bytes = sum(len(data) for data, _ in images)
        return {
            "max_input_tokens": len(prompt.encode("utf-8")) + image_bytes + 4096,
            "max_output_tokens": self.max_output_tokens * len(images),
        }

    def _dispatch_openrouter(self, images: Sequence[ImagePart], *, prompt: str) -> Any:
        parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for data, mime in images:
            parts.append({"type": "image_url", "image_url": {
                "url": f"data:{mime or 'image/png'};base64,{base64.b64encode(data).decode('ascii')}"}})
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": parts},
            ],
            "max_tokens": self.max_output_tokens * len(images),
            "usage": {"include": True},
            "response_format": {"type": "json_object"},
        }
        req = request.Request(f"{self.base_url}/chat/completions",
                              data=json.dumps(payload).encode("utf-8"), method="POST",
                              headers=self._headers())
        return self.guard.invoke(
            role=VISUAL_UNDERSTANDING_OPERATION, provider="openrouter", model=self.model,
            operation=VISUAL_UNDERSTANDING_OPERATION, bounds=self._bounds(images, prompt),
            dispatch=lambda: self._post_json(req, label="OpenRouter vision"),
            extract=_openrouter_identity,
        )

    def _dispatch_gemini(self, images: Sequence[ImagePart], *, prompt: str, batched: bool) -> Any:
        model_resource = self.model if self.model.startswith("models/") else f"models/{self.model}"
        parts: list[dict[str, Any]] = [{"text": prompt}]
        for data, mime in images:
            parts.append({"inline_data": {"mime_type": mime or "image/png",
                                          "data": base64.b64encode(data).decode("ascii")}})
        payload = {
            "systemInstruction": {"parts": [{"text": _SYSTEM}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"maxOutputTokens": self.max_output_tokens * len(images),
                                 "responseMimeType": "application/json",
                                 "responseSchema": _BATCH_SCHEMA if batched else _FIGURE_SCHEMA},
        }
        req = request.Request(f"{self.base_url}/{model_resource}:generateContent",
                              data=json.dumps(payload).encode("utf-8"), method="POST",
                              headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"})
        return self.guard.invoke(
            role=VISUAL_UNDERSTANDING_OPERATION, provider="gemini", model=self.model,
            operation=VISUAL_UNDERSTANDING_OPERATION, bounds=self._bounds(images, prompt),
            dispatch=lambda: self._post_json(req, label="Gemini vision"),
            extract=lambda raw: _gemini_identity(raw, pinned=self.model),
        )

    def _response_text(self, raw: Any) -> str:
        if self.provider == "openrouter":
            try:
                content = raw["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise VisionError("vision response was malformed") from exc
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            if not isinstance(content, str) or not content.strip():
                raise VisionError("vision response had no text content")
            return content
        try:
            parts = raw["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise VisionError("vision response was malformed") from exc
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        if not text.strip():
            raise VisionError("vision response had no text content")
        return text

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                   "Accept": "application/json"}
        if self.referer:
            headers["HTTP-Referer"] = self.referer
        if self.title:
            headers["X-Title"] = self.title
        return headers

    def _post_json(self, req: request.Request, *, label: str) -> Any:
        try:
            with self._opener(req, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise VisionError(f"{label} request failed with status {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise VisionError(f"{label} request failed: {exc.reason}") from exc
        return json.loads(raw) if raw else {}

    def _load_cache(self, key: str) -> VisionUnderstanding | None:
        path = self.cache_dir / f"{key}.json"
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (payload.get("provider") != self.provider or payload.get("model") != self.model
                    or payload.get("prompt_version") != VISION_PROMPT_VERSION):
                return None
            understanding = parse_vision_object(payload["understanding"])
            return replace(understanding,
                           described_by=payload["understanding"].get("described_by")
                           or f"{payload.get('provider')}/{payload.get('model')}")
        except Exception:
            return None

    def _store_cache(self, key: str, understanding: VisionUnderstanding) -> None:
        path = self.cache_dir / f"{key}.json"
        body = json.dumps({
            "provider": self.provider, "model": self.model,
            "prompt_version": VISION_PROMPT_VERSION, "understanding": understanding.__dict__,
        }, ensure_ascii=False, indent=2) + "\n"
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
            temp.write_text(body, encoding="utf-8")
            try:
                os.link(temp, path)
            except FileExistsError:
                pass
            finally:
                temp.unlink(missing_ok=True)
        except OSError:
            pass  # A cache write failure must never fail the figure.


class FallbackVisionClient:
    """Primary provider with an explicit, recorded secondary provider.

    Fallback engages only when the primary raises ``VisionError`` (provider or
    output-contract failure). Ledger errors never fall back: a blocked or
    unknown-settled ledger or an identity mismatch is a routing/accounting
    problem that must surface, not be retried against another model.
    """

    def __init__(self, primary: VisionClient, fallback: VisionClient | None) -> None:
        self.primary = primary
        self.fallback = fallback

    def describe(self, image_bytes: bytes, *, mime: str, context: str = "") -> VisionUnderstanding:
        try:
            return self.primary.describe(image_bytes, mime=mime, context=context)
        except VisionError:
            if self.fallback is None:
                raise
            return self.fallback.describe(image_bytes, mime=mime, context=context)

    def describe_many(self, items: Sequence[Mapping[str, Any]]) -> list[VisionUnderstanding | VisionError]:
        results = self.primary.describe_many(items)
        retry_indices = [index for index, result in enumerate(results) if isinstance(result, VisionError)]
        if not retry_indices or self.fallback is None:
            return results
        fallback_results = self.fallback.describe_many([items[index] for index in retry_indices])
        for index, outcome in zip(retry_indices, fallback_results):
            results[index] = outcome
        return results


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _reported_cost(usage: Mapping[str, Any]) -> Decimal | None:
    raw = usage.get("cost")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return Decimal(str(raw))
    return None


def _openrouter_identity(raw: Any) -> tuple[str | None, dict[str, int | None], Decimal | None]:
    if not isinstance(raw, dict):
        raise VisionError("OpenRouter response was not a JSON object")
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    cached = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    return raw.get("model"), {
        "input_tokens": _int_or_none(usage.get("prompt_tokens")),
        "cached_input_tokens": _int_or_none(cached.get("cached_tokens")),
        "output_tokens": _int_or_none(usage.get("completion_tokens")),
        "cached_output_tokens": None,
        "requests": 1,
    }, _reported_cost(usage)


def _gemini_identity(raw: Any, *, pinned: str) -> tuple[str | None, dict[str, int | None], Decimal | None]:
    if not isinstance(raw, dict):
        raise VisionError("Gemini response was not a JSON object")
    usage = raw.get("usageMetadata") if isinstance(raw.get("usageMetadata"), dict) else {}
    candidates = _int_or_none(usage.get("candidatesTokenCount"))
    thoughts = _int_or_none(usage.get("thoughtsTokenCount"))
    model = raw.get("modelVersion") if isinstance(raw.get("modelVersion"), str) else None
    # Gemini may append a version suffix to the pinned model name; the pinned
    # ID is the identity we accept, and any other route is rejected.
    if model and (model == pinned or model.startswith(f"{pinned}-")):
        model = pinned
    return model, {
        "input_tokens": _int_or_none(usage.get("promptTokenCount")),
        "cached_input_tokens": _int_or_none(usage.get("cachedContentTokenCount")),
        "output_tokens": candidates + thoughts if candidates is not None and thoughts is not None else None,
        "cached_output_tokens": 0,
        "requests": 1,
    }, None


def vision_unavailable_reason(guard: LedgerGuard | None) -> str | None:
    """Why vision calls are skipped; None when a client can be built."""
    if not settings.visual_provider or not settings.visual_model:
        return "vision provider/model not pinned"
    if guard is None:
        return "ledger missing; vision stays off until calls share the run cap"
    return None


def build_vision_client(guard: LedgerGuard | None, *, environ: Mapping[str, str] | None = None) -> VisionClient | FallbackVisionClient | None:
    """Construct the pinned client (with optional recorded fallback); no calls."""
    if vision_unavailable_reason(guard) is not None:
        return None
    source = os.environ if environ is None else environ

    def single(provider: str, model: str) -> VisionClient | None:
        provider = provider.lower()
        if provider == "openrouter":
            api_key = str(source.get("CURRICULUM_TUTOR_OPENROUTER_API_KEY", "")).strip()
            base_url = settings.openrouter_base_url
            referer, title = settings.openrouter_http_referer, settings.openrouter_title
        elif provider == "gemini":
            api_key = str(source.get("CURRICULUM_TUTOR_GEMINI_API_KEY", "")).strip()
            base_url = settings.gemini_base_url
            referer, title = None, None
        else:
            return None
        if not api_key:
            return None
        return VisionClient(
            provider=provider, model=model, api_key=api_key, guard=guard,
            cache_dir=settings.visual_cache_dir, max_output_tokens=settings.visual_max_output_tokens,
            batch_size=max(1, int(getattr(settings, "visual_batch_size", 1))),
            base_url=base_url, referer=referer, title=title,
        )

    primary = single(str(settings.visual_provider), str(settings.visual_model))
    if primary is None:
        return None
    fallback = None
    if settings.visual_fallback_provider and settings.visual_fallback_model:
        fallback = single(str(settings.visual_fallback_provider), str(settings.visual_fallback_model))
    return FallbackVisionClient(primary, fallback) if fallback is not None else primary
