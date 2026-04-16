from __future__ import annotations

import hashlib

from app.services.types import ChunkRecord, NormalizedDocument


def _tokenize(text: str) -> list[str]:
    return text.split()


def _segment_tokens(text: str) -> int:
    return len(_tokenize(text))


def _build_chunk(chunk_index: int, segments: list) -> ChunkRecord:
    chunk_text = "\n\n".join(segment.text for segment in segments).strip()
    normalized_text = chunk_text.lower()
    structure_kinds = [segment.metadata.get("structure_kind", "paragraph") for segment in segments]
    heading_path: list[str] = []
    for segment in segments:
        candidate = segment.metadata.get("heading_path") or []
        if candidate:
            heading_path = candidate
    return ChunkRecord(
        chunk_index=chunk_index,
        text=chunk_text,
        normalized_text=normalized_text,
        token_count=_segment_tokens(chunk_text),
        start_segment=segments[0].segment_number if segments else None,
        end_segment=segments[-1].segment_number if segments else None,
        metadata={
            "dedup_hash": hashlib.sha256(normalized_text.encode("utf-8")).hexdigest(),
            "structure_kinds": structure_kinds,
            "heading_path": heading_path,
            "segment_types": [segment.segment_type for segment in segments],
        },
    )


def _select_overlap_segments(segments: list, overlap_tokens: int) -> list:
    if len(segments) <= 1 or overlap_tokens <= 0:
        return []
    overlap_segments = []
    token_total = 0
    for segment in reversed(segments):
        if token_total >= overlap_tokens:
            break
        overlap_segments.insert(0, segment)
        token_total += _segment_tokens(segment.text)
    if len(overlap_segments) == len(segments):
        return overlap_segments[1:]
    return overlap_segments


def build_chunks(document: NormalizedDocument, *, min_tokens: int = 300, max_tokens: int = 500, overlap_tokens: int = 60) -> list[ChunkRecord]:
    chunks: list[ChunkRecord] = []
    if not document.segments:
        return chunks

    index = 0
    carryover_segments: list = []
    chunk_index = 0
    while index < len(document.segments):
        current_segments = list(carryover_segments)
        current_tokens = sum(_segment_tokens(segment.text) for segment in current_segments)

        while index < len(document.segments):
            segment = document.segments[index]
            segment_tokens = _segment_tokens(segment.text)
            structure_kind = segment.metadata.get("structure_kind", "paragraph")

            if current_segments and structure_kind == "heading" and current_tokens >= min_tokens:
                break

            if current_segments and current_tokens + segment_tokens > max_tokens and current_tokens >= min_tokens:
                break

            current_segments.append(segment)
            current_tokens += segment_tokens
            index += 1

            if segment_tokens > max_tokens and len(current_segments) == len(carryover_segments) + 1:
                break

        if not current_segments:
            current_segments.append(document.segments[index])
            index += 1

        chunk_index += 1
        chunks.append(_build_chunk(chunk_index, current_segments))
        carryover_segments = _select_overlap_segments(current_segments, overlap_tokens) if index < len(document.segments) else []

    return chunks
