"""Recognition-level document-format matrix from the course inventory (T01).

Suffix recognition is not extraction accuracy: this matrix only states which
formats the API accepts and how many files/candidates exist per format. The
per-format extraction and answer studies (T02-T07) need indexed sources and
human structure labels and remain separate selectable modules.
"""

from __future__ import annotations

from typing import Any, Mapping


def format_recognition_matrix(inventory: Mapping[str, Any]) -> dict[str, Any]:
    documents = list(inventory.get("documents") or [])
    by_suffix: dict[str, dict[str, int]] = {}
    for item in documents:
        suffix = item["suffix"] or "(none)"
        row = by_suffix.setdefault(suffix, {"files": 0, "supported_by_api": 0, "unsupported": 0, "empty": 0})
        row["files"] += 1
        row["supported_by_api"] += 1 if item.get("supported") else 0
        row["unsupported"] += 0 if item.get("supported") else 1
        row["empty"] += 1 if item.get("byte_size", 0) == 0 else 0
    courses_with_unsupported = sorted({
        item["course_id"] for item in documents if not item.get("supported")
    })
    return {
        "matrix_version": "1.0",
        "by_suffix": {suffix: by_suffix[suffix] for suffix in sorted(by_suffix)},
        "courses_with_unsupported_files": courses_with_unsupported,
        "status": "recognition_only",
        "note": (
            "Counts come from the read-only inventory. Supported suffixes are accepted by the "
            "upload API; they do not establish accurate extraction of tables, diagrams or reading "
            "order inside those formats."
        ),
    }
