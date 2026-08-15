from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

from app.models.enums import PolicyMode


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PROMPTS_ROOT = REPO_ROOT / "packages" / "prompts"


class PromptConfigurationError(RuntimeError):
    """Raised when the versioned prompt registry cannot be loaded safely."""


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    family: str
    version: str
    text: str


@dataclass(frozen=True, slots=True)
class PromptBundle:
    tutor: PromptTemplate
    guardrail: PromptTemplate
    citation: PromptTemplate
    quiz: PromptTemplate
    policy: PromptTemplate


def get_prompts_root() -> Path:
    configured_root = os.environ.get("CURRICULUM_TUTOR_PROMPTS_ROOT") or os.environ.get("PROMPTS_ROOT")
    return Path(configured_root).expanduser() if configured_root else DEFAULT_PROMPTS_ROOT


def load_policy_prompt(mode: PolicyMode, *, root: Path | None = None) -> PromptTemplate:
    resolved_root = _resolve_root(root)
    registry = _load_registry(resolved_root)
    return _load_template(resolved_root, registry, "policy", mode.value)


def load_prompt_bundle(mode: PolicyMode, *, root: Path | None = None) -> PromptBundle:
    resolved_root = _resolve_root(root)
    registry = _load_registry(resolved_root)
    return PromptBundle(
        tutor=_load_template(resolved_root, registry, "tutor"),
        guardrail=_load_template(resolved_root, registry, "guardrail"),
        citation=_load_template(resolved_root, registry, "citation"),
        quiz=_load_template(resolved_root, registry, "quiz"),
        policy=_load_template(resolved_root, registry, "policy", mode.value),
    )


def compose_tutor_system_prompt(mode: PolicyMode) -> str:
    bundle = load_prompt_bundle(mode)
    return _compose_sections(
        ("Core tutor behavior", bundle.tutor),
        ("Non-negotiable guardrails", bundle.guardrail),
        (f"Active policy mode: {mode.value}", bundle.policy),
        ("Citation behavior", bundle.citation),
    )


def compose_quiz_system_prompt(mode: PolicyMode) -> str:
    bundle = load_prompt_bundle(mode)
    return _compose_sections(
        ("Non-negotiable guardrails", bundle.guardrail),
        (f"Active policy mode: {mode.value}", bundle.policy),
        ("Quiz generation behavior", bundle.quiz),
    )


def _resolve_root(root: Path | None) -> Path:
    resolved_root = (root or get_prompts_root()).resolve()
    if not resolved_root.is_dir():
        raise PromptConfigurationError(f"Prompt root does not exist: {resolved_root}")
    return resolved_root


def _load_registry(root: Path) -> dict[str, Any]:
    registry_path = root / "registry.json"
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PromptConfigurationError(f"Prompt registry is missing: {registry_path}") from exc
    except json.JSONDecodeError as exc:
        raise PromptConfigurationError(f"Prompt registry is not valid JSON: {registry_path}") from exc
    if not isinstance(payload, dict):
        raise PromptConfigurationError("Prompt registry must contain an object")
    return payload


def _load_template(root: Path, registry: dict[str, Any], *family_path: str) -> PromptTemplate:
    entry: Any = registry
    for segment in family_path:
        if not isinstance(entry, dict) or segment not in entry:
            joined = ".".join(family_path)
            raise PromptConfigurationError(f"Prompt registry has no entry for {joined}")
        entry = entry[segment]
    if not isinstance(entry, dict):
        raise PromptConfigurationError(f"Prompt registry entry {'.'.join(family_path)} must be an object")

    version = entry.get("active")
    if not isinstance(version, str) or not version:
        raise PromptConfigurationError(f"Prompt registry entry {'.'.join(family_path)} must declare an active version")
    relative_path = entry.get(version)
    if not isinstance(relative_path, str) or not relative_path:
        raise PromptConfigurationError(f"Prompt registry entry {'.'.join(family_path)} has no path for {version}")

    file_path = (root / relative_path).resolve()
    if root not in file_path.parents or file_path.suffix != ".md":
        raise PromptConfigurationError(f"Prompt path escapes the prompt root or is not Markdown: {relative_path}")
    try:
        text = file_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise PromptConfigurationError(f"Prompt file is missing: {relative_path}") from exc
    if not text:
        raise PromptConfigurationError(f"Prompt file is empty: {relative_path}")
    return PromptTemplate(family=".".join(family_path), version=version, text=text)


def _compose_sections(*sections: tuple[str, PromptTemplate]) -> str:
    return "\n\n".join(
        f"## Runtime section: {label} ({template.family} {template.version})\n{template.text}"
        for label, template in sections
    )
