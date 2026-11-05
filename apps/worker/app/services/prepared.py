"""Batch-scoped prepared documents; never shared across source versions."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from app.services.types import NormalizedDocument, ChunkRecord


@dataclass
class PreparedSource:
    document: NormalizedDocument
    chunks: list[ChunkRecord]
    progress: dict


class PreparedTexts(list):
    def __init__(self, texts, prepared: PreparedSource):
        super().__init__(texts)
        self.prepared = prepared


_sources: ContextVar[dict | None] = ContextVar("prepared_sources", default=None)


@contextmanager
def prepared_sources(sources):
    token = _sources.set(sources)
    try:
        yield
    finally:
        _sources.reset(token)


def take_prepared(source_version_id):
    sources = _sources.get()
    return sources.pop(source_version_id, None) if sources else None
