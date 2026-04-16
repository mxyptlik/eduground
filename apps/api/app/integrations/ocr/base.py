from __future__ import annotations

from abc import ABC, abstractmethod


class OCRAdapter(ABC):
    @abstractmethod
    def extract_text(self, raw_bytes: bytes, *, filename: str, mime_type: str) -> str:
        raise NotImplementedError
