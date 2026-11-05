"""Deterministic validation of explicit answer-to-evidence citation links.

Retrieval cards are not links. Callers must supply markers observed in the
answer and their claim association; semantic support is judged separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class CitationTarget:
    chunk_id: str
    source_id: str
    locator: str | None = None


@dataclass(frozen=True)
class CitationLink:
    claim_id: str
    marker: str
    chunk_id: str
    source_id: str
    locator: str | None = None


@dataclass(frozen=True)
class CitationCheck:
    claim_id: str
    marker: str
    status: str
    reason: str | None


def validate_citation_targets(
    links: Sequence[CitationLink], *,
    context_chunk_ids: set[str],
    known_targets: Mapping[str, CitationTarget],
) -> tuple[CitationCheck, ...]:
    """Flag missing, unrelated, wrong-source or wrong-locator targets.

    A valid target says nothing about whether the passage actually supports
    the attached claim. That requires the later sufficiency/correctness judge.
    """
    checks: list[CitationCheck] = []
    for link in links:
        if not all((link.claim_id.strip(), link.marker.strip(), link.chunk_id.strip(), link.source_id.strip())):
            raise ValueError("citation link identifiers must be nonempty")
        target = known_targets.get(link.chunk_id)
        if target is None:
            status, reason = "missing_target", "citation chunk does not exist"
        elif link.chunk_id not in context_chunk_ids:
            status, reason = "out_of_context", "citation chunk was not in the answer context"
        elif target.source_id != link.source_id:
            status, reason = "wrong_source", "citation source differs from the chunk source"
        elif link.locator is not None and target.locator != link.locator:
            status, reason = "wrong_locator", "citation locator differs from the indexed chunk"
        else:
            status, reason = "valid_target", None
        checks.append(CitationCheck(link.claim_id, link.marker, status, reason))
    return tuple(checks)
