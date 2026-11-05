"""Offline contract checks for the local corpus builder helpers."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import fitz

from tools.evaluation.prepare_profile_prices import merge_registries

from tools.evaluation.build_local_corpus import (
    build_chunk_rows,
    build_qdrant_point_id,
    chunk_request_entry,
    resume_entry,
    runtime_payload_fields,
    upsert_file_entry,
)


def _chunk(index: int, text: str = "Evidence text", segment: int = 1) -> dict:
    return {
        "chunk_index": index,
        "text": text,
        "normalized_text": text.lower(),
        "token_count": len(text.split()),
        "start_segment": segment,
        "end_segment": segment,
        "metadata": {"dedup_hash": f"hash-{index}", "heading_path": ["Unit 1"]},
    }


def test_point_id_matches_worker_uuid5_scheme() -> None:
    source_version_id = "4f22c61e-d2f1-484e-8c81-d70e623fcab4"

    point_id = build_qdrant_point_id(source_version_id, 3)

    assert point_id == str(uuid5(NAMESPACE_URL, f"{source_version_id}:3"))
    assert point_id != build_qdrant_point_id(source_version_id, 4)
    assert point_id != build_qdrant_point_id("52b41c66-2f11-4d81-b9a0-1f1f2a3b4c5d", 3)
    assert len(point_id) == 36 and point_id.count("-") == 4


def test_chunk_rows_mirror_worker_db_row_fields_for_pdf_and_pptx() -> None:
    chunks = [_chunk(1, "A\x00B evidence"), _chunk(2, "Second paragraph", segment=2)]

    rows = build_chunk_rows(source_id="source-1", source_version_id="version-1",
                            module_id=None, source_type="pdf", chunks=chunks)

    assert [row["chunk_index"] for row in rows] == [1, 2]
    assert rows[0]["text"] == "AB evidence" and rows[0]["char_count"] == len("AB evidence")
    assert rows[0]["qdrant_point_id"] == build_qdrant_point_id("version-1", 1)
    assert rows[0]["start_page"] == 1 and rows[0]["start_slide"] is None
    assert rows[1]["start_page"] == 2 and rows[1]["end_slide"] is None
    assert rows[0]["heading_path"] == ["Unit 1"]
    assert rows[0]["tags"]["dedup_hash"] == "hash-1"
    assert len({row["id"] for row in rows}) == 2

    slides = build_chunk_rows(source_id="source-1", source_version_id="version-1",
                              module_id=None, source_type="pptx", chunks=chunks)

    assert slides[0]["start_slide"] == 1 and slides[0]["start_page"] is None
    assert slides[1]["end_slide"] == 2 and slides[1]["end_page"] is None


def test_resume_only_skips_completed_entries_with_matching_sha256() -> None:
    entries = [
        {"course_id": "C", "relative_path": "C/lecture.pdf", "sha256": "a" * 64,
         "status": "complete", "source_id": "s1", "chunk_count": 5},
        {"course_id": "C", "relative_path": "C/failed.pdf", "sha256": "b" * 64,
         "status": "embedding_failed", "source_id": "s2", "chunk_count": 3},
    ]

    assert resume_entry(entries, course_id="C", relative_path="C/lecture.pdf", sha256="a" * 64) is entries[0]
    assert resume_entry(entries, course_id="C", relative_path="C/lecture.pdf", sha256="c" * 64) is None
    assert resume_entry(entries, course_id="C", relative_path="C/failed.pdf", sha256="b" * 64) is None
    assert resume_entry(entries, course_id="OTHER", relative_path="C/lecture.pdf", sha256="a" * 64) is None
    assert resume_entry([], course_id="C", relative_path="C/lecture.pdf", sha256="a" * 64) is None


def test_manifest_upsert_replaces_the_entry_for_the_same_path() -> None:
    entries = [{"course_id": "C", "relative_path": "C/a.pdf", "sha256": "a" * 64, "status": "complete"}]

    upsert_file_entry(entries, {"course_id": "C", "relative_path": "C/a.pdf", "sha256": "b" * 64,
                                "status": "complete", "chunk_count": 7})
    upsert_file_entry(entries, {"course_id": "C", "relative_path": "C/b.pdf", "sha256": "c" * 64,
                                "status": "complete"})

    assert len(entries) == 2
    replaced = next(entry for entry in entries if entry["relative_path"] == "C/a.pdf")
    assert replaced["sha256"] == "b" * 64 and replaced["chunk_count"] == 7


def test_runtime_payload_fields_match_the_api_read_schema() -> None:
    chunk = {"db_chunk_id": "db-chunk-1", "start_page": 4, "end_page": 5,
             "start_slide": None, "end_slide": None}

    fields = runtime_payload_fields(chunk)

    assert fields == {
        "chunk_id": "db-chunk-1",
        "status": "indexed",
        "start_page": 4,
        "end_page": 5,
        "start_slide": None,
        "end_slide": None,
    }


def test_chunk_request_maps_pdf_pages_and_pptx_slides_to_segments() -> None:
    pdf_row = {"chunk_index": 1, "text": "t", "normalized_text": "t", "token_count": 1,
               "start_page": 2, "end_page": 3, "start_slide": None, "end_slide": None,
               "id": "db-1", "qdrant_point_id": "q-1", "tags": {}}
    pptx_row = {**pdf_row, "start_page": None, "end_page": None, "start_slide": 6, "end_slide": 7}

    pdf_entry = chunk_request_entry(pdf_row, "pdf")
    pptx_entry = chunk_request_entry(pptx_row, "pptx")

    assert pdf_entry["start_segment"] == 2 and pdf_entry["end_segment"] == 3
    assert pdf_entry["db_chunk_id"] == "db-1" and pdf_entry["qdrant_point_id"] == "q-1"
    assert pptx_entry["start_segment"] == 6 and pptx_entry["end_segment"] == 7
    assert pptx_entry["start_slide"] == 6 and pptx_entry["start_page"] is None


def test_parse_child_uses_worker_ocr_before_chunking_scanned_pdf(tmp_path) -> None:
    root = Path(__file__).resolve().parents[3]
    with fitz.open() as document:
        document.new_page()
        source = tmp_path / "scanned.pdf"
        source.write_bytes(document.tobytes())
    script = "\n".join([
        "import sys",
        "from pathlib import Path",
        "from types import SimpleNamespace",
        f"sys.path.insert(0, {str(root / 'apps' / 'worker')!r})",
        "from app import ledger",
        "from app.services import ocr",
        "ledger.load_ledger_guard = lambda: SimpleNamespace(calls=[])",
        "def fake_ocr(raw, *, filename, mime_type, guard, telemetry):",
        "    assert filename == 'scanned.pdf' and mime_type == 'application/pdf' and guard is not None",
        "    telemetry.append({'provider': 'openrouter', 'status': 'complete', 'model': 'deepseek/deepseek-v4.1-flash'})",
        "    return 'The scanned lecture explains evidence retrieval. ' * 40",
        "ocr.run_ocr = fake_ocr",
        "from tools.evaluation.build_local_corpus import parse_child",
        "raise SystemExit(parse_child(Path(sys.argv[1]), enable_ocr=True))",
    ])
    result = subprocess.run([sys.executable, "-c", script, str(source)], cwd=root,
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    row = json.loads(result.stdout.strip().splitlines()[-1])
    assert row["ocr_completed_provider"] == "deepseek/deepseek-v4.1-flash"
    assert row["chunk_count"] > 0 and row["status"] == "parsed_and_chunked"


def test_deepseek_only_corpus_preflight_uses_shared_ledger_without_dispatch(tmp_path) -> None:
    root = Path(__file__).resolve().parents[3]
    registry = merge_registries([root / "evaluation" / "prices" / "2026-09-30.deepseek-ocr.json"])
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps(registry), encoding="utf-8")
    ledger = tmp_path / "budget.sqlite3"
    env = {**os.environ, "CURRICULUM_TUTOR_UNSTRUCTURED_API_KEY": "",
           "CURRICULUM_TUTOR_OPENROUTER_API_KEY": "offline-test-key",
           "CURRICULUM_TUTOR_OCR_VISION_ENABLED": "true",
           "EDUGROUND_EVAL_BUDGET_LEDGER": str(ledger),
           "EDUGROUND_EVAL_MAX_API_SPEND_USD": "10",
           "EDUGROUND_EVAL_PRICE_REGISTRY": str(prices)}
    result = subprocess.run([sys.executable, str(root / "tools" / "evaluation" / "build_local_corpus.py"),
                             "--_preflight", "--ocr"], cwd=root, env=env, capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(ledger) as db:
        assert db.execute("SELECT count(*) FROM calls").fetchone()[0] == 0
