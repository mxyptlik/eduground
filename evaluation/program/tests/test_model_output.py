"""Evaluation-side model JSON validator contract (mirror of the worker gate)."""

from __future__ import annotations

import pytest

from evaluation.program.model_output import ModelOutputError, validate_model_json


def test_validator_accepts_compliant_payloads_and_fences():
    assert validate_model_json('{"items": []}') == {"items": []}
    assert validate_model_json('```json\n{"items": [1]}\n```', required_keys=("items",))["items"] == [1]
    assert validate_model_json('[{"a": 1}]', expect="array")[0]["a"] == 1


def test_validator_rejects_non_json_empty_and_ambiguous_forms():
    cases = [
        ("", "empty"),
        ("   ", "empty"),
        ("```json\n```", "empty after removing code fences"),
        ("Kimi did its best", "not valid JSON"),
        ('{"items": NaN}', "non-finite JSON constant"),
        ('{"items": 1, "items": 2}', "repeats JSON key"),
        ("[]", "must be a JSON object"),
        ("{}", "empty JSON object"),
        ('{"other": 1}', "missing required keys: items"),
        ('{"items": "   "}', "empty required keys: items"),
    ]
    for payload, match in cases:
        with pytest.raises(ModelOutputError, match=match):
            validate_model_json(payload, label="Kimi output", required_keys=("items",))
