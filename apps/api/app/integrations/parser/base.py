from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True)
class ParsedSegment:
    segment_type: str
    segment_number: int | None
    title: str | None
    text: str


@dataclass(slots=True)
class ParsedDocument:
    text: str
    segments: list[ParsedSegment]


class ParserAdapter(ABC):
    @abstractmethod
    def parse(self, raw_bytes: bytes, filename: str) -> ParsedDocument:
        raise NotImplementedError

