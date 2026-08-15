from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def _api_path() -> Path:
    return Path(__file__).resolve().parents[2] / "apps" / "api"


sys.path.insert(0, str(_api_path()))

from app.db.session import SessionLocal  # noqa: E402
from app.evaluation.ragas_dataset import (  # noqa: E402
    ChunkRecord,
    DatasetValidationError,
    SourceRecord,
    build_draft_dataset,
)
from app.models.content import Chunk, Source  # noqa: E402
from app.models.enums import SourceStatus  # noqa: E402


def _locator(chunk: Chunk) -> str:
    if chunk.start_slide is not None:
        end = chunk.end_slide if chunk.end_slide is not None else chunk.start_slide
        return f"slide {chunk.start_slide}" if end == chunk.start_slide else f"slides {chunk.start_slide}-{end}"
    if chunk.start_page is not None:
        end = chunk.end_page if chunk.end_page is not None else chunk.start_page
        return f"page {chunk.start_page}" if end == chunk.start_page else f"pages {chunk.start_page}-{end}"
    return "indexed chunk"


def _source_locator(chunks: list[Chunk]) -> str:
    locators = sorted({_locator(chunk) for chunk in chunks})
    return "; ".join(locators[:12]) if locators else "indexed source"


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a source-traceable draft RAGAS dataset from one indexed EduGround notebook.")
    parser.add_argument("--notebook-id", required=True)
    parser.add_argument("--output", type=Path, default=Path("evaluation/ragas/datasets/eduground_100q.v1.draft.json"))
    parser.add_argument("--dataset-id", default="eduground-ragas-100q")
    parser.add_argument("--version", default="v1-draft")
    parser.add_argument("--sample-count", type=int, default=100)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.output.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite {args.output}. Use --force only for an intentional replacement.")

    with SessionLocal() as db:
        sources = (
            db.query(Source)
            .filter(Source.notebook_id == args.notebook_id, Source.status == SourceStatus.INDEXED, Source.deleted_at.is_(None))
            .order_by(Source.title.asc())
            .all()
        )
        chunks = (
            db.query(Chunk)
            .filter(Chunk.source_id.in_([source.id for source in sources]))
            .order_by(Chunk.source_id.asc(), Chunk.chunk_index.asc())
            .all()
        )

    chunks_by_source: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        chunks_by_source.setdefault(chunk.source_id, []).append(chunk)
    source_records = [
        SourceRecord(
            source_id=source.id,
            source_label=source.title,
            sha256=source.checksum_sha256,
            notebook_id=args.notebook_id,
            page_or_slide_range=_source_locator(chunks_by_source.get(source.id, [])),
        )
        for source in sources
    ]
    chunk_records = [
        ChunkRecord(
            source_id=chunk.source_id,
            source_label=next(source.title for source in sources if source.id == chunk.source_id),
            text=chunk.text,
            locator=_locator(chunk),
            chunk_id=chunk.id,
        )
        for chunk in chunks
    ]
    try:
        dataset = build_draft_dataset(
            dataset_id=args.dataset_id,
            version=args.version,
            sources=source_records,
            chunks=chunk_records,
            sample_count=args.sample_count,
        )
    except DatasetValidationError as exc:
        raise SystemExit(f"Dataset creation failed: {exc}") from exc

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dataset, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "dataset_id": args.dataset_id,
                "sources": len(source_records),
                "indexed_chunks": len(chunk_records),
                "sample_count": len(dataset["samples"]),
                "review_status": "draft",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
