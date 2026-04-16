from __future__ import annotations

import re

from app.services.types import NormalizedDocument, NormalizedSegment


def normalize_document(document: NormalizedDocument) -> NormalizedDocument:
    normalized_segments = []
    for segment in document.segments:
        cleaned = re.sub(r"\s+", " ", segment.text).strip()
        normalized_segments.append(
            NormalizedSegment(
                segment_type=segment.segment_type,
                segment_number=segment.segment_number,
                title=segment.title,
                text=cleaned,
                metadata=dict(segment.metadata),
            )
        )
    full_text = "\n\n".join(segment.text for segment in normalized_segments)
    return NormalizedDocument(text=full_text, segments=normalized_segments, metadata=dict(document.metadata))
