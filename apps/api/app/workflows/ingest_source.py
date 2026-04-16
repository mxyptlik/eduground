from __future__ import annotations

from dataclasses import dataclass

from app.models.content import IngestionJob, Source


@dataclass(slots=True)
class IngestSourceWorkflowResult:
    source: Source
    initial_job: IngestionJob

