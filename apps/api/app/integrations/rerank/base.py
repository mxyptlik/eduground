from __future__ import annotations

from abc import ABC, abstractmethod


class RerankerProvider(ABC):
    @abstractmethod
    def rerank(self, query: str, items: list[dict]) -> list[dict]:
        raise NotImplementedError

