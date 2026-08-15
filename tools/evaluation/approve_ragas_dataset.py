from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.db.session import SessionLocal  # noqa: E402
from app.evaluation.ragas_dataset import DatasetValidationError, validate_dataset  # noqa: E402
from app.models.content import Chunk, Source  # noqa: E402
from app.models.enums import SourceStatus  # noqa: E402


def _normalized(value: str) -> str:
    return " ".join(value.split()).strip()


def _verify_source_integrity(dataset: dict[str, Any]) -> dict[str, int]:
    manifest_by_id = {source["source_id"]: source for source in dataset["source_manifest"]}
    chunk_ids = [sample["source_chunk_id"] for sample in dataset["samples"]]
    with SessionLocal() as db:
        for source_id, manifest in manifest_by_id.items():
            source = db.get(Source, source_id)
            if source is None or source.status != SourceStatus.INDEXED or source.deleted_at is not None:
                raise DatasetValidationError(f"Frozen source {source_id} is not currently indexed")
            if source.notebook_id != manifest["notebook_id"] or source.checksum_sha256 != manifest["sha256"]:
                raise DatasetValidationError(f"Frozen source {source_id} no longer matches its manifest")
        chunks = {chunk.id: chunk for chunk in db.query(Chunk).filter(Chunk.id.in_(chunk_ids)).all()}
        for sample in dataset["samples"]:
            chunk = chunks.get(sample["source_chunk_id"])
            if chunk is None or chunk.source_id not in sample["source_ids"]:
                raise DatasetValidationError(f"Sample {sample['sample_id']} no longer has its approved source chunk")
            normalized_chunk = _normalized(chunk.text)
            if not all(_normalized(context) in normalized_chunk for context in sample["reference_contexts"]):
                raise DatasetValidationError(f"Sample {sample['sample_id']} has reference evidence outside its source chunk")
    return {"sources_verified": len(manifest_by_id), "chunks_verified": len(chunks)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Approve a RAGAS dataset only after deterministic source-integrity checks. This is not a human-quality review."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite {args.output}")

    try:
        dataset = json.loads(args.input.read_text(encoding="utf-8"))
        validate_dataset(dataset, require_approved=False)
        checks = _verify_source_integrity(dataset)
    except (OSError, json.JSONDecodeError, DatasetValidationError) as exc:
        raise SystemExit(f"Dataset cannot be approved: {exc}") from exc

    reviewed_at = datetime.now(UTC).isoformat()
    for sample in dataset["samples"]:
        sample["review_status"] = "approved"
        sample["reviewer"] = "automated source-integrity gate (not a human review)"
        sample["reviewed_at_utc"] = reviewed_at
    dataset["approval"] = {
        "approval_type": "automated source-integrity gate",
        "human_review_completed": False,
        "approved_at_utc": reviewed_at,
        "checks": {
            **checks,
            "question_count": len(dataset["samples"]),
            "duplicate_questions": False,
            "reference_contexts_verified_against_current_chunks": True,
        },
        "known_limitation": "Items were Gemini-authored and automatically source-verified; they were not independently human-reviewed.",
    }
    validate_dataset(dataset, require_approved=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dataset, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "sample_count": len(dataset["samples"]), **checks}, indent=2))


if __name__ == "__main__":
    main()
