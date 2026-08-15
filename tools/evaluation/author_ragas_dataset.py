from __future__ import annotations

import argparse
from collections import defaultdict, deque
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sys
import time
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from app.db.session import SessionLocal  # noqa: E402
from app.evaluation.ragas_dataset import (  # noqa: E402
    ChunkRecord,
    DatasetValidationError,
    SourceRecord,
    validate_dataset,
)
from app.integrations.llm.base import GeminiLLMProvider  # noqa: E402
from app.models.content import Chunk, Source  # noqa: E402
from app.models.enums import SourceStatus  # noqa: E402


_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_SPACE_PATTERN = re.compile(r"\s+")


def _locator(chunk: Chunk) -> str:
    if chunk.start_slide is not None:
        end = chunk.end_slide if chunk.end_slide is not None else chunk.start_slide
        return f"slide {chunk.start_slide}" if end == chunk.start_slide else f"slides {chunk.start_slide}-{end}"
    if chunk.start_page is not None:
        end = chunk.end_page if chunk.end_page is not None else chunk.start_page
        return f"page {chunk.start_page}" if end == chunk.start_page else f"pages {chunk.start_page}-{end}"
    return "indexed chunk"


def _source_locator(chunks: list[Chunk]) -> str:
    return "; ".join(sorted({_locator(chunk) for chunk in chunks})[:12]) or "indexed source"


def _normalize(value: str) -> str:
    return _SPACE_PATTERN.sub(" ", value).strip().lower()


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = _FENCE_PATTERN.sub("", text.strip()).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Gemini did not return a JSON object")
    payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Gemini output root was not an object")
    return payload


def _author_prompt(chunk: ChunkRecord, item_limit: int, *, supplemental_pass: bool) -> str:
    supplemental_instruction = ""
    if supplemental_pass:
        supplemental_instruction = """
This is a supplemental pass. Select different facts, relationships, or comparisons from the excerpt than a typical first-pass definition question. Do not repeat a question about the excerpt's main heading."""
    return f"""You are authoring a high-quality, source-grounded RAG evaluation dataset.

Using only the excerpt below, return JSON with exactly this shape:
{{"items": [{{"question": "...", "reference_answer": "...", "reference_quote": "...", "difficulty": "easy|medium|hard"}}]}}

Create exactly {item_limit} distinct items when the excerpt contains enough academic content. Return an empty items array only when the excerpt is unsuitable.
Rules:
- Test academic concepts, relationships, definitions, processes, or comparisons actually stated in the excerpt.
- Do not ask about slide titles, instructions, formatting, names, course administration, or the task of filling a worksheet.
- Each question must be specific, standalone, answerable from one short factual answer, and non-duplicative.
- Cover different concepts or relationships from the excerpt; do not restate the same fact across items.
- Each reference answer must be concise, complete, and supported only by the excerpt.
- reference_quote must be an exact, continuous quote copied from the excerpt that directly supports the reference answer.
- Do not use knowledge outside the excerpt. Do not add explanation or Markdown outside the JSON object.
{supplemental_instruction}

Source: {chunk.source_label}
Locator: {chunk.locator}
Excerpt:
---
{chunk.text}
---"""


def _valid_item(raw: Any, chunk: ChunkRecord, known_questions: set[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    question = str(raw.get("question") or "").strip()
    answer = str(raw.get("reference_answer") or "").strip()
    quote = str(raw.get("reference_quote") or "").strip()
    difficulty = str(raw.get("difficulty") or "mixed").strip().lower()
    if not (12 <= len(question) <= 280 and 20 <= len(answer) <= 500 and 20 <= len(quote) <= 900):
        return None
    if question.endswith(":") or not question.endswith("?"):
        return None
    normalized_question = _normalize(question)
    if normalized_question in known_questions:
        return None
    if _normalize(quote) not in _normalize(chunk.text):
        return None
    if difficulty not in {"easy", "medium", "hard"}:
        difficulty = "mixed"
    known_questions.add(normalized_question)
    return {
        "question": question,
        "reference_answer": answer,
        "reference_contexts": [quote],
        "source_ids": [chunk.source_id],
        "expected_locator": chunk.locator,
        "question_type": "source-grounded factual",
        "difficulty": difficulty,
        "authoring_method": "Gemini source-bounded draft authoring",
        "review_status": "draft",
        "source_chunk_id": chunk.chunk_id,
        "supporting_quote": quote,
    }


def _round_robin_chunks(chunks: list[ChunkRecord]) -> list[ChunkRecord]:
    by_source: dict[str, deque[ChunkRecord]] = defaultdict(deque)
    for chunk in chunks:
        by_source[chunk.source_id].append(chunk)
    ordered: list[ChunkRecord] = []
    source_ids = sorted(by_source)
    while any(by_source.values()):
        for source_id in source_ids:
            if by_source[source_id]:
                ordered.append(by_source[source_id].popleft())
    return ordered


def main() -> None:
    parser = argparse.ArgumentParser(description="Author a source-bounded draft RAGAS dataset with Gemini.")
    parser.add_argument("--notebook-id", required=True)
    parser.add_argument("--output", type=Path, default=Path("evaluation/ragas/datasets/eduground_100q.v1.assisted.draft.json"))
    parser.add_argument("--dataset-id", default="eduground-ragas-100q")
    parser.add_argument("--version", default="v1-assisted-draft")
    parser.add_argument("--sample-count", type=int, default=100)
    parser.add_argument("--items-per-chunk", type=int, default=4)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--max-chunks", type=int)
    parser.add_argument("--max-passes", type=int, default=2)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.output.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite {args.output}. Use --force only for an intentional replacement.")
    if args.items_per_chunk < 1:
        raise SystemExit("--items-per-chunk must be positive")
    if args.max_passes < 1:
        raise SystemExit("--max-passes must be positive")

    with SessionLocal() as db:
        sources = (
            db.query(Source)
            .filter(Source.notebook_id == args.notebook_id, Source.status == SourceStatus.INDEXED, Source.deleted_at.is_(None))
            .order_by(Source.title.asc())
            .all()
        )
        source_ids = [source.id for source in sources]
        chunks = db.query(Chunk).filter(Chunk.source_id.in_(source_ids)).order_by(Chunk.source_id, Chunk.chunk_index).all()

    chunks_by_source: dict[str, list[Chunk]] = defaultdict(list)
    source_by_id = {source.id: source for source in sources}
    for chunk in chunks:
        chunks_by_source[chunk.source_id].append(chunk)
    source_manifest = [
        SourceRecord(
            source_id=source.id,
            source_label=source.title,
            sha256=source.checksum_sha256,
            notebook_id=args.notebook_id,
            page_or_slide_range=_source_locator(chunks_by_source[source.id]),
        )
        for source in sources
    ]
    chunk_records = _round_robin_chunks(
        [
            ChunkRecord(
                source_id=chunk.source_id,
                source_label=source_by_id[chunk.source_id].title,
                text=chunk.text,
                locator=_locator(chunk),
                chunk_id=chunk.id,
            )
            for chunk in chunks
        ]
    )
    if args.max_chunks is not None:
        chunk_records = chunk_records[: args.max_chunks]

    llm = GeminiLLMProvider()
    llm.config.max_output_tokens = args.max_output_tokens
    known_questions: set[str] = set()
    samples: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for pass_number in range(1, args.max_passes + 1):
        for position, chunk in enumerate(chunk_records, start=1):
            try:
                response = llm.generate_messages(
                    [{"role": "user", "content": _author_prompt(chunk, args.items_per_chunk, supplemental_pass=pass_number > 1)}]
                )
                raw_items = _extract_json(response).get("items")
                if not isinstance(raw_items, list):
                    raise ValueError("Gemini JSON did not contain an items array")
                for raw_item in raw_items:
                    item = _valid_item(raw_item, chunk, known_questions)
                    if item is None:
                        continue
                    item["sample_id"] = f"eduground-ragas-{len(samples) + 1:03d}"
                    samples.append(item)
                    if len(samples) == args.sample_count:
                        break
            except Exception as exc:  # Keep the authoring record and continue to another chunk.
                failures.append({"chunk_id": chunk.chunk_id, "pass": str(pass_number), "error": str(exc)})
            if len(samples) == args.sample_count:
                break
            if args.delay_seconds > 0 and position < len(chunk_records):
                time.sleep(args.delay_seconds)
        if len(samples) == args.sample_count:
            break

    payload = {
        "dataset_id": args.dataset_id,
        "version": args.version,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source_manifest": [asdict(source) for source in source_manifest],
        "samples": samples,
        "authoring_failures": failures,
    }
    try:
        validate_dataset(payload, require_approved=False, expected_sample_count=args.sample_count)
    except DatasetValidationError as exc:
        partial_path = args.output.with_name(f"{args.output.stem}.partial{args.output.suffix}")
        partial_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        raise SystemExit(
            f"Authoring stopped with {len(samples)} accepted items. Partial draft was written to {partial_path}: {exc}"
        ) from exc

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "samples": len(samples), "sources": len(source_manifest), "failures": len(failures), "review_status": "draft"}, indent=2))


if __name__ == "__main__":
    main()
