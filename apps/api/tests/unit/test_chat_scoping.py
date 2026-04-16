from __future__ import annotations

from app.models.enums import SourceStatus
from app.policies.scoping import build_retrieval_filters


def test_build_retrieval_filters_keeps_notebook_scope_and_selected_ids():
    filters = build_retrieval_filters(
        "notebook-1",
        source_ids=["source-1", "source-2"],
        module_ids=["module-1"],
    )

    assert filters["notebook_id"] == "notebook-1"
    assert filters["status"] == SourceStatus.INDEXED.value
    assert filters["source_ids"] == ["source-1", "source-2"]
    assert filters["module_ids"] == ["module-1"]
