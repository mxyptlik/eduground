"""Structural validation of model JSON output (vision or text).

Never trust a provider's "JSON mode": every JSON-producing call routes through
``validate_model_json``, which rejects empty or whitespace-only content, code
fences, invalid JSON, NaN/Infinity constants, duplicate object keys, the wrong
top-level type, and missing/empty required fields. A failure raises
``ModelOutputError`` with a bounded excerpt, so callers fail closed instead of
building partial results from a non-compliant response.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Literal


class ModelOutputError(ValueError):
    """The model response is not acceptable JSON under the requested contract."""


def _reject_constant(value: str) -> None:
    raise ModelOutputError(f"model output contains non-finite JSON constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise ModelOutputError(f"model output repeats JSON key: {key!r}")
        seen.add(key)
    return dict(pairs)


def validate_model_json(
    value: Any, *,
    label: str = "model output",
    expect: Literal["object", "array"] = "object",
    required_keys: Iterable[str] = (),
    require_nonempty: bool = True,
) -> Any:
    """Parse and structurally validate one model JSON response.

    Returns the parsed payload only when every check passes. Strings are
    accepted with optional ```json fences (models sometimes add them even in
    JSON mode); anything else is rejected.
    """
    if not isinstance(value, str):
        raise ModelOutputError(f"{label} is not text")
    text = value.strip()
    if not text:
        raise ModelOutputError(f"{label} is empty")
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
        if not text:
            raise ModelOutputError(f"{label} is empty after removing code fences")
    try:
        payload = json.loads(text, parse_constant=_reject_constant,
                             object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ModelOutputError(f"{label} is not valid JSON: {text[:160]!r}") from exc
    expected_type = dict if expect == "object" else list
    if not isinstance(payload, expected_type):
        raise ModelOutputError(f"{label} must be a JSON {expect}, got {type(payload).__name__}")
    if require_nonempty and not payload:
        raise ModelOutputError(f"{label} is an empty JSON {expect}")
    keys = tuple(required_keys)
    if keys:
        if expect != "object":
            raise ModelOutputError("required_keys only apply to JSON objects")
        missing = [key for key in keys if key not in payload]
        if missing:
            raise ModelOutputError(f"{label} is missing required keys: {', '.join(missing)}")
        blank = [key for key in keys
                 if isinstance(payload.get(key), str) and not str(payload[key]).strip()]
        if blank:
            raise ModelOutputError(f"{label} has empty required keys: {', '.join(blank)}")
    return payload
