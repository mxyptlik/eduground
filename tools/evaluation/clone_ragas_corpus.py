from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.db.session import SessionLocal  # noqa: E402
from app.integrations.queue import enqueue_source_ingestion_job  # noqa: E402
from app.models.content import IngestionJob, Source, SourceVersion  # noqa: E402
from app.models.curriculum import Notebook, NotebookMembership  # noqa: E402
from app.models.enums import IngestionJobType, JobStatus, NotebookMembershipRole, NotebookVisibility, SourceStatus  # noqa: E402
from app.models.identity import User  # noqa: E402
from app.models.enums import PolicyMode  # noqa: E402
from app.models.operations import ModelProfile  # noqa: F401, E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an isolated evaluation notebook by cloning source metadata and immutable storage references."
    )
    parser.add_argument("--source-notebook-id", required=True)
    parser.add_argument("--title", default="CSC442 RAGAS evaluation - Gemini")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite {args.output}")

    with SessionLocal() as db:
        source_notebook = db.get(Notebook, args.source_notebook_id)
        if source_notebook is None:
            raise SystemExit("Source notebook was not found")
        owner = db.get(User, source_notebook.owner_user_id)
        if owner is None:
            raise SystemExit("Source notebook owner was not found")
        sources = (
            db.query(Source)
            .filter(
                Source.notebook_id == source_notebook.id,
                Source.status == SourceStatus.INDEXED,
                Source.deleted_at.is_(None),
            )
            .order_by(Source.title.asc())
            .all()
        )
        if not sources:
            raise SystemExit("Source notebook has no indexed sources to clone")

        notebook = Notebook(
            institution_id=source_notebook.institution_id,
            course_offering_id=source_notebook.course_offering_id,
            title=args.title,
            description="Isolated, source-only corpus for the reproducible Gemini RAGAS evaluation.",
            owner_user_id=owner.id,
            visibility=NotebookVisibility.PRIVATE,
            policy_mode=PolicyMode.TEACHING,
        )
        db.add(notebook)
        db.flush()
        db.add(
            NotebookMembership(
                notebook_id=notebook.id,
                user_id=owner.id,
                role=NotebookMembershipRole.OWNER,
                granted_by_user_id=owner.id,
                created_at=datetime.now(UTC),
            )
        )

        cloned: list[tuple[Source, SourceVersion, IngestionJob]] = []
        for source in sources:
            clone = Source(
                notebook_id=notebook.id,
                module_id=source.module_id,
                source_type=source.source_type,
                title=source.title,
                original_filename=source.original_filename,
                storage_key=source.storage_key,
                mime_type=source.mime_type,
                checksum_sha256=source.checksum_sha256,
                byte_size=source.byte_size,
                language_code=source.language_code,
                version_number=1,
                status=SourceStatus.PROCESSING,
                is_authoritative=source.is_authoritative,
                source_origin=source.source_origin,
                ingestion_profile_id=source.ingestion_profile_id,
                created_by_user_id=owner.id,
            )
            db.add(clone)
            db.flush()
            version = SourceVersion(
                source_id=clone.id,
                version_number=1,
                storage_key=source.storage_key,
                checksum_sha256=source.checksum_sha256,
                status=SourceStatus.PROCESSING.value,
                created_at=datetime.now(UTC),
                created_by_user_id=owner.id,
            )
            db.add(version)
            db.flush()
            job = IngestionJob(
                source_id=clone.id,
                source_version_id=version.id,
                job_type=IngestionJobType.PARSE,
                status=JobStatus.QUEUED,
                attempt_count=0,
                stage="queued",
                storage_key_snapshot=source.storage_key,
                progress_json={"cloned_from_source_id": source.id, "cloned_for": "ragas_evaluation"},
                created_at=datetime.now(UTC),
            )
            db.add(job)
            cloned.append((clone, version, job))
        db.commit()

        for clone, version, job in cloned:
            enqueue_source_ingestion_job(
                job_id=job.id,
                source_id=clone.id,
                source_version_id=version.id,
                institution_id=notebook.institution_id,
            )

        payload = {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "source_notebook_id": source_notebook.id,
            "evaluation_notebook_id": notebook.id,
            "source_count": len(cloned),
            "sources": [
                {
                    "source_id": clone.id,
                    "title": clone.title,
                    "sha256": clone.checksum_sha256,
                    "ingestion_job_id": job.id,
                }
                for clone, _version, job in cloned
            ],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
