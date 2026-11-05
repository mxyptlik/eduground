"""Live source/chunk verification for reviewer releases."""
from __future__ import annotations

from typing import Any

from evaluation.program.reviews import ReviewError


class DatabaseEvidenceResolver:
    def __init__(self, session: Any, *, notebook_id: str):
        self.session = session
        self.notebook_id = notebook_id
        self._current_versions: dict[str, str] = {}

    def verify_source(self, manifest: dict[str, Any]) -> None:
        from app.models.content import Source, SourceVersion
        from app.models.enums import SourceStatus

        source = self.session.get(Source, manifest["source_id"])
        if source is None or source.deleted_at is not None or source.status != SourceStatus.INDEXED:
            raise ReviewError(f"Source {manifest['source_id']} is not currently indexed")
        if source.notebook_id != self.notebook_id or source.notebook_id != manifest["notebook_id"]:
            raise ReviewError(f"Source {source.id} is outside the evaluation notebook")
        if source.checksum_sha256.lower() != str(manifest["sha256"]).lower():
            raise ReviewError(f"Source {source.id} checksum changed since the review draft")
        if manifest.get("version_number") is not None and source.version_number != manifest["version_number"]:
            raise ReviewError(f"Source {source.id} version changed since the review draft")
        versions = (
            self.session.query(SourceVersion)
            .filter(SourceVersion.source_id == source.id, SourceVersion.version_number == source.version_number)
            .all()
        )
        if (len(versions) != 1 or versions[0].checksum_sha256.lower() != source.checksum_sha256.lower()
                or versions[0].status != SourceStatus.INDEXED.value):
            raise ReviewError(f"Source {source.id} current version is missing or inconsistent")
        self._current_versions[source.id] = versions[0].id

    def chunk(self, chunk_id: str) -> tuple[str, str]:
        from app.models.content import Chunk

        chunk = self.session.get(Chunk, chunk_id)
        if chunk is None:
            raise ReviewError(f"Indexed evidence chunk {chunk_id} no longer exists")
        if self._current_versions.get(chunk.source_id) != chunk.source_version_id:
            raise ReviewError(f"Indexed evidence chunk {chunk_id} is not from the verified current version")
        return chunk.source_id, chunk.text
