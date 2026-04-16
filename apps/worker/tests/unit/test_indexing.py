from __future__ import annotations

from app.services.indexing import build_qdrant_payload, build_qdrant_point
from app.services.types import ChunkRecord


def test_build_qdrant_payload_carries_scope_metadata() -> None:
    chunk = ChunkRecord(
        chunk_index=3,
        text="A chunk",
        normalized_text="a chunk",
        token_count=2,
        start_segment=1,
        end_segment=2,
        metadata={"dedup_hash": "abc123"},
    )

    payload = build_qdrant_payload(
        chunk,
        notebook_id="notebook-1",
        institution_id="institution-1",
        source_id="source-1",
        source_version_id="version-1",
        module_id="module-1",
    )

    assert payload["chunk_id"] == "source-1:3"
    assert payload["module_id"] == "module-1"
    assert payload["dedup_hash"] == "abc123"


def test_build_qdrant_point_uses_source_version_scoped_id() -> None:
    chunk = ChunkRecord(
        chunk_index=7,
        text="A chunk",
        normalized_text="a chunk",
        token_count=2,
        start_segment=1,
        end_segment=2,
        metadata={"dedup_hash": "abc123"},
    )

    point = build_qdrant_point(
        chunk,
        [0.1, 0.2, 0.3],
        notebook_id="notebook-1",
        institution_id="institution-1",
        source_id="source-1",
        source_version_id="version-1",
    )

    assert point["id"] == "version-1:7"
    assert point["vector"] == [0.1, 0.2, 0.3]
    assert point["payload"]["source_version_id"] == "version-1"
