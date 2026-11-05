"""Re-embed and index local-corpus files that never got vectors.

Context: during the 2026-09-30 pilot ingestion a provider call was killed
mid-flight, the shared budget ledger marked it unknown and then blocked every
subsequent embedding call. Whole courses were parsed, chunked and persisted
but ended up with no vectors and no Qdrant points. The unknown attempt has
been reconciled; this pass finishes the job for every manifest entry that is
``complete`` (or ``embedding_failed``) but missing embedding/index status.

It reads chunk rows from the local corpus SQLite, embeds them through the
worker's own services and the shared $10 ledger, rebuilds the deterministic
uuid5 points with the worker's builders, upserts them, and marks the manifest
entry complete. No parsing, no uploads, no Postgres, no Clerk.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "evaluation" / "local_corpus" / "corpus-manifest" / "manifest.json"
DEFAULT_DB = ROOT / "evaluation" / "local_corpus" / "corpus.sqlite3"
DEFAULT_LEDGER = ROOT / "evaluation" / "pilot" / "budget.sqlite3"
DEFAULT_PRICES = ROOT / "evaluation" / "prices" / "2026-09-30.openrouter.json"


def _worker_path() -> None:
    """Mirror build_local_corpus: the worker's ``app`` package must win."""
    sys.path.insert(0, str(ROOT / "apps" / "worker"))
    for name in ("api", "evaluator"):
        conflicting = str(ROOT / "apps" / name)
        while conflicting in sys.path:
            sys.path.remove(conflicting)
    sys.path.insert(0, str(ROOT / "tools" / "evaluation"))


def needs_vectors(entry: dict) -> bool:
    """Entries whose file is done but whose vectors/points are not."""
    if entry.get("status") not in ("complete", "embedding_failed"):
        return False
    return entry.get("embedding_status") != "complete" or entry.get("index_status") != "complete"


def write_manifest(path: Path, manifest: dict) -> None:
    manifest["updated_at_utc"] = datetime.now(UTC).isoformat()
    body = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(body, encoding="utf-8")
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--collection", default="eduground_chunks")
    parser.add_argument("--batch-size", type=int, default=64, help="chunks per embedding request")
    parser.add_argument("--limit", type=int, help="only the first N entries (smoke runs)")
    args = parser.parse_args()

    os.environ["EDUGROUND_EVAL_BUDGET_LEDGER"] = str(args.ledger.resolve())
    os.environ.setdefault("EDUGROUND_EVAL_MAX_API_SPEND_USD", "15")
    os.environ["EDUGROUND_EVAL_PRICE_REGISTRY"] = str(args.prices.resolve())
    os.environ.setdefault("CURRICULUM_TUTOR_QDRANT_COLLECTION_NAME", args.collection)

    _worker_path()
    import build_local_corpus as blc
    from app.ledger import INGESTION_EMBEDDING_OPERATION, load_ledger_guard
    from app.services.embeddings import embed_texts
    from app.services.vectorstore import ensure_collection, upsert_points
    from app.settings import settings

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    all_entries = manifest.get("files", [])
    entries = [entry for entry in all_entries if needs_vectors(entry)]
    if args.limit:
        entries = entries[: args.limit]
    print(f"manifest entries: {len(all_entries)} | needing vectors: {len(entries)}")
    if not entries:
        return 0

    guard = load_ledger_guard()
    if guard is None:
        print("embedding ledger unavailable (EDUGROUND_EVAL_BUDGET_LEDGER)")
        return 2
    guard.schedule(provider="openrouter", model=settings.openrouter_embedding_model,
                   operation=INGESTION_EMBEDDING_OPERATION)

    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    done = 0
    failed: list[tuple[str, str]] = []
    for entry in entries:
        relative_path = entry.get("relative_path", "?")
        source_id = entry.get("source_id")
        if not source_id:
            failed.append((relative_path, "no source_id in manifest"))
            continue
        source = conn.execute(
            "SELECT s.notebook_id, s.source_type, n.institution_id "
            "FROM sources s JOIN notebooks n ON n.id = s.notebook_id WHERE s.id = ?",
            (source_id,),
        ).fetchone()
        if source is None:
            failed.append((relative_path, "source row missing from corpus db"))
            continue
        rows = conn.execute(
            "SELECT * FROM chunks WHERE source_id = ? ORDER BY chunk_index", (source_id,)
        ).fetchall()
        if not rows:
            failed.append((relative_path, "no chunk rows in corpus db"))
            continue
        chunk_dicts = []
        for row in rows:
            record = dict(row)
            if isinstance(record.get("tags"), str):
                try:
                    record["tags"] = json.loads(record["tags"])
                except ValueError:
                    record["tags"] = {}
            chunk_dicts.append(record)
        request = {
            "scope": {
                "notebook_id": source["notebook_id"],
                "institution_id": source["institution_id"],
                "source_id": source_id,
                "source_version_id": chunk_dicts[0]["source_version_id"],
                "module_id": chunk_dicts[0]["module_id"],
            },
            "chunks": [blc.chunk_request_entry(record, source["source_type"]) for record in chunk_dicts],
        }
        try:
            texts = [chunk["text"] for chunk in request["chunks"]]
            vectors: list[list[float]] = []
            batch = max(1, args.batch_size)
            for start in range(0, len(texts), batch):
                group = texts[start : start + batch]
                got = embed_texts(group, guard=guard)
                if len(got) != len(group):
                    raise RuntimeError(f"embedding count mismatch {len(got)} != {len(group)}")
                vectors.extend(got)
            points = blc.build_provider_points(request, vectors)
            ensure_collection(len(vectors[0]))
            upsert_points(points)
        except Exception as exc:  # noqa: BLE001 - isolate per-file failures
            failed.append((relative_path, f"{type(exc).__name__}: {str(exc)[:180]}"))
            print(f"FAILED {relative_path} | {type(exc).__name__}: {str(exc)[:180]}", flush=True)
            continue
        entry["status"] = "complete"
        entry["embedding_status"] = "complete"
        entry["index_status"] = "complete"
        entry["indexed_points"] = len(points)
        entry["error"] = None
        entry["reembedded_at_utc"] = datetime.now(UTC).isoformat()
        done += 1
        print(f"[{done}/{len(entries)}] {relative_path} -> {len(points)} points", flush=True)
        write_manifest(args.manifest, manifest)

    state = guard.ledger.state()
    summary = {
        "reembedded_files": done,
        "failed_files": [{"path": path, "error": error} for path, error in failed],
        "ledger": {
            "settled_usd": str(state.settled_usd),
            "reserved_usd": str(state.reserved_usd),
            "unknown_attempts": state.unknown_attempts,
        },
    }
    manifest.setdefault("reembed", {})["last_run"] = {
        "at_utc": datetime.now(UTC).isoformat(),
        **summary,
    }
    write_manifest(args.manifest, manifest)
    print(json.dumps(summary, indent=2), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
