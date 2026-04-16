from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from app.services.types import ChunkRecord


def build_qdrant_payload(chunk: ChunkRecord, *, notebook_id: str, institution_id: str, source_id: str, source_version_id: str, module_id: str | None = None) -> dict:
    return {
        "chunk_index": chunk.chunk_index,
        "chunk_id": f"{source_id}:{chunk.chunk_index}",
        "source_id": source_id,
        "source_version_id": source_version_id,
        "notebook_id": notebook_id,
        "institution_id": institution_id,
        "module_id": module_id,
        "segment_start": chunk.start_segment,
        "segment_end": chunk.end_segment,
        "token_count": chunk.token_count,
        "dedup_hash": chunk.metadata["dedup_hash"],
    }


def build_qdrant_point(chunk: ChunkRecord, vector: list[float], *, notebook_id: str, institution_id: str, source_id: str, source_version_id: str, module_id: str | None = None) -> dict:
    return {
        "id": str(uuid5(NAMESPACE_URL, f"{source_version_id}:{chunk.chunk_index}")),
        "vector": vector,
        "payload": build_qdrant_payload(
            chunk,
            notebook_id=notebook_id,
            institution_id=institution_id,
            source_id=source_id,
            source_version_id=source_version_id,
            module_id=module_id,
        ),
    }
