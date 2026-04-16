from __future__ import annotations

from app.pipelines import ingestion_pipeline as pipeline
from app import telemetry
from app.services.types import NormalizedDocument, NormalizedSegment


def test_run_ingestion_pipeline_indexes_chunks(monkeypatch, markdown_source_bytes: bytes) -> None:
    ensured_sizes: list[int] = []
    upserted_points: list[dict] = []
    telemetry.reset_test_state()

    monkeypatch.setattr(pipeline, "embed_texts", lambda texts: [[0.1, 0.2, 0.3] for _ in texts])
    monkeypatch.setattr(pipeline, "ensure_collection", lambda size: ensured_sizes.append(size))
    monkeypatch.setattr(pipeline, "upsert_points", lambda points: upserted_points.extend(points))

    result = pipeline.run_ingestion_pipeline(
        markdown_source_bytes,
        "lecture.md",
        notebook_id="notebook-1",
        institution_id="institution-1",
        source_id="source-1",
        source_version_id="version-1",
        mime_type="text/markdown",
    )

    assert result["indexed_chunk_count"] == len(result["chunks"])
    assert result["point_ids"] == [point["id"] for point in upserted_points]
    assert ensured_sizes == [3]
    assert upserted_points
    stage_names = {event.attributes["stage"] for event in telemetry.get_metric_events() if "stage" in event.attributes}
    assert {"pipeline.parse", "pipeline.normalize", "pipeline.chunk", "pipeline.embed", "pipeline.index"} <= stage_names


def test_run_ingestion_pipeline_invokes_ocr_on_fallback(monkeypatch) -> None:
    calls: list[str] = []

    first_pass = NormalizedDocument(
        text="",
        segments=[],
        metadata={"requires_fallback_ocr": True, "extraction_method": "pdfplumber"},
    )
    second_pass = NormalizedDocument(
        text="Recovered OCR text",
        segments=[
            NormalizedSegment(
                segment_type="content",
                segment_number=1,
                title=None,
                text="Recovered OCR text with enough tokens to become a chunk",
                metadata={"structure_kind": "paragraph", "heading_path": [], "contains_structure": False},
            )
        ],
        metadata={"requires_fallback_ocr": False, "extraction_method": "ocr-text"},
    )

    def fake_parse(raw_bytes: bytes, filename: str):
        calls.append(filename)
        return first_pass if filename == "scan.pdf" else second_pass

    monkeypatch.setattr(pipeline, "parse_source_bytes", fake_parse)
    monkeypatch.setattr(pipeline, "run_ocr", lambda raw_bytes, filename, mime_type: "Recovered OCR text")
    monkeypatch.setattr(pipeline, "embed_texts", lambda texts: [[0.1, 0.2] for _ in texts])
    monkeypatch.setattr(pipeline, "ensure_collection", lambda size: None)
    monkeypatch.setattr(pipeline, "upsert_points", lambda points: None)

    result = pipeline.run_ingestion_pipeline(
        b"%PDF-1.4",
        "scan.pdf",
        notebook_id="notebook-1",
        institution_id="institution-1",
        source_id="source-1",
        source_version_id="version-1",
        mime_type="application/pdf",
    )

    assert calls == ["scan.pdf", "scan.pdf.ocr.txt"]
    assert result["indexed_chunk_count"] == 1


def test_run_ingestion_pipeline_is_deterministic_for_point_ids(monkeypatch, markdown_source_bytes: bytes) -> None:
    monkeypatch.setattr(pipeline, "embed_texts", lambda texts: [[0.1, 0.2] for _ in texts])
    monkeypatch.setattr(pipeline, "ensure_collection", lambda size: None)
    monkeypatch.setattr(pipeline, "upsert_points", lambda points: None)

    first = pipeline.run_ingestion_pipeline(
        markdown_source_bytes,
        "lecture.md",
        notebook_id="notebook-1",
        institution_id="institution-1",
        source_id="source-1",
        source_version_id="version-1",
        mime_type="text/markdown",
    )
    second = pipeline.run_ingestion_pipeline(
        markdown_source_bytes,
        "lecture.md",
        notebook_id="notebook-1",
        institution_id="institution-1",
        source_id="source-1",
        source_version_id="version-1",
        mime_type="text/markdown",
    )

    assert first["point_ids"] == second["point_ids"]
