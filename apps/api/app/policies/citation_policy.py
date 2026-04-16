from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CitationValidationResult:
    is_valid: bool
    coverage_ratio: float
    reason: str | None = None


class CitationPolicy:
    def __init__(self, minimum_coverage: float = 1.0) -> None:
        self.minimum_coverage = minimum_coverage

    def validate(self, factual_blocks: int, citation_count: int) -> CitationValidationResult:
        if factual_blocks <= 0:
            return CitationValidationResult(is_valid=True, coverage_ratio=1.0)
        coverage_ratio = min(citation_count / factual_blocks, 1.0)
        if coverage_ratio < self.minimum_coverage:
            return CitationValidationResult(
                is_valid=False,
                coverage_ratio=coverage_ratio,
                reason="Citation coverage below threshold",
            )
        return CitationValidationResult(is_valid=True, coverage_ratio=coverage_ratio)

