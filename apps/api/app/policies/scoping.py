from __future__ import annotations

from app.models.content import Source
from app.models.enums import SourceStatus


def build_retrieval_filters(
    notebook_id: str,
    *,
    source_ids: list[str] | None = None,
    module_ids: list[str] | None = None,
) -> dict:
    filters: dict[str, object] = {"notebook_id": notebook_id, "status": SourceStatus.INDEXED.value}
    if source_ids:
        filters["source_ids"] = source_ids
    if module_ids:
        filters["module_ids"] = module_ids
    return filters


def source_is_active(source: Source) -> bool:
    return source.status not in {SourceStatus.ARCHIVED, SourceStatus.DELETED}
