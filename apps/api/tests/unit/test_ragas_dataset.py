from __future__ import annotations

import pytest

from app.evaluation.ragas_dataset import DatasetValidationError, validate_dataset


pytestmark = pytest.mark.unit


def _dataset(*, review_status: str = "approved") -> dict:
    source_id = "source-1"
    return {
        "dataset_id": "eduground-ragas-100q",
        "version": "v1",
        "source_manifest": [
            {
                "source_id": source_id,
                "source_label": "Biology source",
                "sha256": "a" * 64,
                "notebook_id": "notebook-1",
                "page_or_slide_range": "pages 1-10",
            }
        ],
        "samples": [
            {
                "sample_id": f"sample-{number:03d}",
                "question": f"What concept is described by source statement number {number}?",
                "reference_answer": f"Source statement {number} describes the relevant academic concept.",
                "reference_contexts": [f"Source statement {number} describes the relevant academic concept."],
                "source_ids": [source_id],
                "source_chunk_id": f"chunk-{number:03d}",
                "review_status": review_status,
            }
            for number in range(1, 101)
        ],
    }


def test_final_ragas_dataset_requires_one_hundred_approved_samples() -> None:
    validate_dataset(_dataset(), require_approved=True)


def test_final_ragas_dataset_rejects_unreviewed_drafts() -> None:
    with pytest.raises(DatasetValidationError, match="not approved"):
        validate_dataset(_dataset(review_status="draft"), require_approved=True)


def test_dataset_rejects_sources_outside_the_frozen_manifest() -> None:
    dataset = _dataset()
    dataset["samples"][0]["source_ids"] = ["unexpected-source"]

    with pytest.raises(DatasetValidationError, match="outside the frozen manifest"):
        validate_dataset(dataset, require_approved=True)
