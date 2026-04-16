from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class NormalizedSegment:
    segment_type: str
    segment_number: int | None
    title: str | None
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedDocument:
    text: str
    segments: list[NormalizedSegment]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChunkRecord:
    chunk_index: int
    text: str
    normalized_text: str
    token_count: int
    start_segment: int | None
    end_segment: int | None
    metadata: dict[str, Any] = field(default_factory=dict)
