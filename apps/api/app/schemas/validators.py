from __future__ import annotations

import re
from typing import Any


UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
MIME_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,127}/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,127}$")
LANGUAGE_CODE_PATTERN = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8}){0,2}$")
CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
DANGEROUS_MARKUP_PATTERN = re.compile(r"(?i)(<\s*script\b|<\s*iframe\b|javascript:|data:\s*text/html)")


def normalize_text(
    value: str,
    *,
    field_name: str,
    max_length: int,
    min_length: int = 1,
    allow_newlines: bool = False,
    allow_dangerous_markup: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if len(normalized) < min_length:
        raise ValueError(f"{field_name} cannot be empty")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} exceeds the maximum length of {max_length}")
    if CONTROL_CHAR_PATTERN.search(normalized):
        raise ValueError(f"{field_name} contains control characters")
    if not allow_newlines and any(character in normalized for character in ("\r", "\n", "\t")):
        raise ValueError(f"{field_name} cannot contain tabs or newlines")
    if not allow_dangerous_markup and DANGEROUS_MARKUP_PATTERN.search(normalized):
        raise ValueError(f"{field_name} contains blocked markup")
    return normalized


def normalize_optional_text(
    value: str | None,
    *,
    field_name: str,
    max_length: int,
    allow_newlines: bool = False,
    allow_dangerous_markup: bool = False,
) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalize_text(
        normalized,
        field_name=field_name,
        max_length=max_length,
        allow_newlines=allow_newlines,
        allow_dangerous_markup=allow_dangerous_markup,
    )


def validate_uuid_string(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = normalize_text(value, field_name=field_name, max_length=36)
    if not UUID_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a valid UUID")
    return normalized.lower()


def validate_uuid_list(values: list[str], *, field_name: str, max_items: int = 50) -> list[str]:
    if len(values) > max_items:
        raise ValueError(f"{field_name} cannot contain more than {max_items} items")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized_value = validate_uuid_string(value, field_name=field_name)
        assert normalized_value is not None
        if normalized_value in seen:
            continue
        seen.add(normalized_value)
        normalized.append(normalized_value)
    return normalized


def validate_filename(value: str, *, field_name: str = "filename") -> str:
    normalized = normalize_text(value, field_name=field_name, max_length=255)
    if any(separator in normalized for separator in ("/", "\\", ":", "*", "?", "\"", "<", ">", "|")):
        raise ValueError(f"{field_name} contains invalid path characters")
    if ".." in normalized:
        raise ValueError(f"{field_name} cannot contain path traversal sequences")
    return normalized


def validate_storage_key(value: str) -> str:
    normalized = normalize_text(value, field_name="storage_key", max_length=512)
    if normalized.startswith("/") or "\\" in normalized or ".." in normalized or "//" in normalized:
        raise ValueError("storage_key is invalid")
    return normalized


def validate_checksum_sha256(value: str) -> str:
    normalized = normalize_text(value, field_name="checksum_sha256", max_length=64)
    if not SHA256_PATTERN.fullmatch(normalized):
        raise ValueError("checksum_sha256 must be a 64 character hex digest")
    return normalized.lower()


def validate_mime_type(value: str) -> str:
    normalized = normalize_text(value, field_name="mime_type", max_length=255)
    if not MIME_TYPE_PATTERN.fullmatch(normalized):
        raise ValueError("mime_type is invalid")
    return normalized.lower()


def validate_language_code(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = normalize_text(value, field_name="language_code", max_length=32)
    if not LANGUAGE_CODE_PATTERN.fullmatch(normalized):
        raise ValueError("language_code is invalid")
    return normalized


def validate_password_strength(value: str) -> str:
    normalized = normalize_text(value, field_name="password", max_length=256, min_length=10)
    if not any(character.isalpha() for character in normalized):
        raise ValueError("password must include at least one letter")
    if not any(character.isdigit() for character in normalized):
        raise ValueError("password must include at least one digit")
    return normalized


def validate_answer_payload(value: dict[str, Any], *, field_name: str = "answers") -> dict[str, Any]:
    if len(value) > 100:
        raise ValueError(f"{field_name} cannot contain more than 100 entries")
    normalized: dict[str, Any] = {}
    for key, answer in value.items():
        question_id = validate_uuid_string(key, field_name=f"{field_name} key")
        if question_id is None:
            raise ValueError(f"{field_name} keys must be valid UUIDs")
        if not isinstance(answer, dict):
            raise ValueError(f"{field_name} values must be objects")
        cleaned_answer: dict[str, Any] = {}
        choice = answer.get("choice")
        if choice is not None:
            cleaned_answer["choice"] = normalize_text(
                str(choice),
                field_name=f"{field_name}.choice",
                max_length=500,
                allow_newlines=False,
            )
        normalized[question_id] = cleaned_answer
    return normalized
