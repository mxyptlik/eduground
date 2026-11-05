"""Tests for the recognition-level format matrix."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.program.reports.format_matrix import format_recognition_matrix

REPORT = Path(__file__).resolve().parents[2] / "course_intake" / "inventory.2026-09-30.json"


def test_matrix_counts_real_inventory_when_present():
    path = REPORT.resolve()
    if not path.is_file():
        return  # The real snapshot is optional; synthetic coverage below is authoritative.
    inventory = json.loads(path.read_text(encoding="utf-8"))
    matrix = format_recognition_matrix(inventory)
    assert matrix["status"] == "recognition_only"
    summary = inventory["summary"]
    assert sum(row["files"] for row in matrix["by_suffix"].values()) == summary["file_count"]
    assert sum(row["unsupported"] for row in matrix["by_suffix"].values()) == summary["unsupported_count"]


def test_matrix_classifies_supported_unsupported_and_empty():
    inventory = {
        "documents": [
            {"course_id": "A", "suffix": ".pdf", "supported": True, "byte_size": 10},
            {"course_id": "A", "suffix": ".ppt", "supported": False, "byte_size": 10},
            {"course_id": "B", "suffix": ".ppt", "supported": False, "byte_size": 10},
            {"course_id": "B", "suffix": ".txt", "supported": True, "byte_size": 0},
        ]
    }
    matrix = format_recognition_matrix(inventory)
    assert matrix["by_suffix"][".ppt"] == {"files": 2, "supported_by_api": 0, "unsupported": 2, "empty": 0}
    assert matrix["by_suffix"][".txt"]["empty"] == 1
    assert matrix["courses_with_unsupported_files"] == ["A", "B"]
    assert "not establish accurate extraction" in matrix["note"]
