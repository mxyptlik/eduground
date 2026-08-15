from __future__ import annotations

import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
from typing import Any
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.evaluation.ragas_dataset import DatasetValidationError, validate_dataset  # noqa: E402
from app.models.content import Chunk, Source  # noqa: E402
from app.models.curriculum import Notebook  # noqa: E402
from app.models.enums import SourceStatus  # noqa: E402
from app.models.identity import User  # noqa: E402
from app.models.learning import Note  # noqa: E402
from app.services.chat import ChatService  # noqa: E402


def _normalized(value: str) -> str:
    return " ".join(value.split()).strip()


def _read_dataset(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        validate_dataset(payload, require_approved=True)
    except (OSError, json.JSONDecodeError, DatasetValidationError) as exc:
        raise RuntimeError(f"The dataset is not eligible for collection: {exc}") from exc
    return payload


def _ensure_frozen_sources(db, dataset: dict[str, Any], notebook_id: str) -> None:
    for source_entry in dataset["source_manifest"]:
        source = db.get(Source, source_entry["source_id"])
        if source is None:
            raise RuntimeError(f"Frozen source {source_entry['source_id']} no longer exists")
        if source.notebook_id != notebook_id:
            raise RuntimeError(f"Frozen source {source.id} is no longer in the selected notebook")
        if source.status != SourceStatus.INDEXED:
            raise RuntimeError(f"Frozen source {source.title} is not indexed")
        if source.checksum_sha256 != source_entry["sha256"]:
            raise RuntimeError(f"Frozen source checksum changed for {source.title}")

    for sample in dataset["samples"]:
        reference_chunk = db.get(Chunk, sample["source_chunk_id"])
        if reference_chunk is None:
            raise RuntimeError(f"Reference chunk for {sample['sample_id']} no longer exists")
        if reference_chunk.source_id not in sample["source_ids"]:
            raise RuntimeError(f"Reference chunk for {sample['sample_id']} belongs to an unexpected source")
        normalized_chunk = _normalized(reference_chunk.text)
        if not all(_normalized(context) in normalized_chunk for context in sample["reference_contexts"]):
            raise RuntimeError(f"Reference evidence for {sample['sample_id']} no longer matches its frozen chunk")


def _ensure_controlled_gemini_configuration() -> None:
    issues: list[str] = []
    if not settings.gemini_api_key:
        issues.append("CURRICULUM_TUTOR_GEMINI_API_KEY is not configured")
    if not settings.gemini_fallback_enabled:
        issues.append("CURRICULUM_TUTOR_GEMINI_FALLBACK_ENABLED must be true")
    if settings.openrouter_api_key:
        issues.append("CURRICULUM_TUTOR_OPENROUTER_API_KEY must be unavailable for the controlled Gemini run")
    if settings.reranker_enabled:
        issues.append("CURRICULUM_TUTOR_RERANKER_ENABLED must be false because EduGround has no Gemini reranker")
    if settings.qdrant_collection_name == "eduground_chunks":
        issues.append("CURRICULUM_TUTOR_QDRANT_COLLECTION_NAME must name a new dedicated collection, not eduground_chunks")
    if issues:
        raise RuntimeError("Controlled Gemini evaluation configuration is not ready: " + "; ".join(issues))


def _runtime_retrieval_config(notebook_id: str) -> dict[str, Any]:
    return {
        "run_target": "local_eduground_internal_workflow",
        "api_public_base_url": settings.api_public_base_url,
        "workflow": "app.services.chat.ChatService.post_message",
        "notebook_id": notebook_id,
        "vector_database": "Qdrant",
        "qdrant_url": settings.qdrant_url,
        "collection_name": settings.qdrant_collection_name,
        "retrieval_mode": "dense",
        "top_k_requested": 5,
        "reranker_enabled": settings.reranker_enabled,
        "chat_provider_chain": {
            "primary": "openrouter",
            "primary_model": settings.openrouter_chat_model,
            "fallback_enabled": settings.gemini_fallback_enabled,
            "fallback": "gemini" if settings.gemini_fallback_enabled else None,
            "fallback_model": settings.gemini_chat_model if settings.gemini_fallback_enabled else None,
        },
        "embedding_provider_chain": {
            "primary": "openrouter",
            "primary_model": settings.openrouter_embedding_model,
            "fallback_enabled": settings.gemini_fallback_enabled,
            "fallback": "gemini" if settings.gemini_fallback_enabled else None,
            "fallback_model": settings.gemini_embedding_model if settings.gemini_fallback_enabled else None,
            "fallback_output_dimensionality": settings.gemini_embedding_output_dimensionality
            if settings.gemini_fallback_enabled
            else None,
        },
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def _run_command(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _copy_run_inputs(*, dataset_path: Path, run_dir: Path, dataset: dict[str, Any], notebook_id: str, run_id: str) -> None:
    dataset_copy = run_dir / "dataset.json"
    dataset_copy.write_bytes(dataset_path.read_bytes())
    (run_dir / "dataset.sha256").write_text(sha256(dataset_copy.read_bytes()).hexdigest() + "\n", encoding="utf-8")
    _write_json(run_dir / "source-manifest.json", {"notebook_id": notebook_id, "sources": dataset["source_manifest"]})
    _write_json(run_dir / "retrieval-config.json", _runtime_retrieval_config(notebook_id))
    _write_json(
        run_dir / "evaluator-config.json",
        {
            "framework": "RAGAS",
            "metrics": ["faithfulness", "context_recall"],
            "evaluator_provider": "Google Gemini",
            "evaluator_model": settings.gemini_chat_model,
            "max_retries": 2,
            "concurrency": 1,
            "run_id": run_id,
        },
    )
    _write_json(
        run_dir / "manifest.json",
        {
            "run_id": run_id,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "dataset_id": dataset["dataset_id"],
            "dataset_version": dataset["version"],
            "python_executable": sys.executable,
            "python_version": sys.version,
            "platform": platform.platform(),
            "api_public_base_url": settings.api_public_base_url,
            "notes": [
                "The collector uses the same ChatService and AnswerQuestionWorkflow as local API requests.",
                "No API key, token, session cookie, or signed upload URL is retained in this run bundle.",
            ],
        },
    )
    (run_dir / "command.txt").write_text(" ".join(sys.argv) + "\n", encoding="utf-8")
    freeze = _run_command([sys.executable, "-m", "pip", "freeze"])
    (run_dir / "requirements.freeze.txt").write_text(freeze["stdout"] + "\n", encoding="utf-8")
    _write_json(
        run_dir / "runtime-provenance.json",
        {
            "git_revision": _run_command(["git", "-c", f"safe.directory={REPO_ROOT}", "rev-parse", "HEAD"]),
            "docker_containers": _run_command(
                ["docker", "ps", "--format", "{{json .}}"]
            ),
            "pip_freeze": {"returncode": freeze["returncode"], "stderr": freeze["stderr"]},
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect live EduGround answer and retrieval evidence for an approved RAGAS dataset.")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--notebook-id", required=True)
    parser.add_argument("--user-id", help="Defaults to the notebook owner. The evaluation user must have no notebook notes.")
    parser.add_argument("--delay-seconds", type=float, default=5.0, help="Delay between live answers to respect provider request limits.")
    args = parser.parse_args()

    if args.run_dir.exists():
        raise SystemExit(f"Refusing to write into an existing run directory: {args.run_dir}")
    if args.delay_seconds < 0:
        raise SystemExit("--delay-seconds must be zero or greater")
    try:
        dataset = _read_dataset(args.dataset)
        _ensure_controlled_gemini_configuration()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    source_notebook_ids = {entry["notebook_id"] for entry in dataset["source_manifest"]}
    if source_notebook_ids != {args.notebook_id}:
        raise SystemExit("--notebook-id does not match the dataset's frozen source manifest")

    run_id = uuid4().hex
    failures: list[dict[str, str]] = []
    with SessionLocal() as db:
        notebook = db.get(Notebook, args.notebook_id)
        if notebook is None:
            raise SystemExit("Evaluation notebook was not found")
        user = db.get(User, args.user_id or notebook.owner_user_id)
        if user is None:
            raise SystemExit("Evaluation user was not found")
        _ensure_frozen_sources(db, dataset, args.notebook_id)
        note_count = db.query(Note).filter(Note.notebook_id == args.notebook_id).count()
        if note_count:
            raise SystemExit(
                "The evaluation notebook contains notes. Use a dedicated source-only notebook so retrieved_contexts exactly represent source chunks."
            )

        args.run_dir.mkdir(parents=True, exist_ok=False)
        _copy_run_inputs(
            dataset_path=args.dataset,
            run_dir=args.run_dir,
            dataset=dataset,
            notebook_id=args.notebook_id,
            run_id=run_id,
        )
        chat = ChatService(db)
        session = chat.create_session(args.notebook_id, f"RAGAS evaluation {run_id}", user)
        with (args.run_dir / "ragas-inputs.jsonl").open("w", encoding="utf-8") as output:
            for index, sample in enumerate(dataset["samples"], start=1):
                try:
                    _, answer = chat.post_message(
                        session.id,
                        sample["question"],
                        selected_source_ids=[],
                        selected_module_ids=[],
                        user=user,
                    )
                    trace = answer.retrieval_trace
                    if trace is None or not trace.items:
                        raise RuntimeError("The live workflow returned no retrieval trace items")
                    chunk_ids = [item.chunk_id for item in trace.items]
                    chunk_by_id = {chunk.id: chunk for chunk in db.query(Chunk).filter(Chunk.id.in_(chunk_ids)).all()}
                    retrieved_contexts = [chunk_by_id[chunk_id].text for chunk_id in chunk_ids if chunk_id in chunk_by_id]
                    if len(retrieved_contexts) != len(chunk_ids):
                        raise RuntimeError("A retrieved chunk was unavailable while collecting evidence")
                    record = {
                        "run_id": run_id,
                        "sample_id": sample["sample_id"],
                        "user_input": sample["question"],
                        "reference": sample["reference_answer"],
                        "reference_contexts": sample["reference_contexts"],
                        "reference_context_ids": [sample["source_chunk_id"]],
                        "response": answer.content_markdown,
                        "retrieved_contexts": retrieved_contexts,
                        "retrieved_context_ids": chunk_ids,
                        "citations": [citation.model_dump(mode="json") for citation in answer.citations],
                        "retrieval_trace": trace.model_dump(mode="json"),
                    }
                    output.write(json.dumps(record, ensure_ascii=True) + "\n")
                    output.flush()
                    print(json.dumps({"sample": index, "sample_id": sample["sample_id"], "status": "collected"}))
                except Exception as exc:
                    failures.append({"sample_id": sample["sample_id"], "error": str(exc)})
                    print(json.dumps({"sample": index, "sample_id": sample["sample_id"], "status": "failed"}))
                if args.delay_seconds > 0 and index < len(dataset["samples"]):
                    time.sleep(args.delay_seconds)

    _write_json(
        args.run_dir / "collection-summary.json",
        {"run_id": run_id, "sample_count": len(dataset["samples"]), "failed_samples": failures},
    )
    if failures:
        raise SystemExit(f"Collection completed with {len(failures)} failures. Review collection-summary.json before scoring.")


if __name__ == "__main__":
    main()
