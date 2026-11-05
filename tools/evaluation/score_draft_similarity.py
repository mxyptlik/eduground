"""Soft similarity alarm for authored drafts. It never rejects anything.

After the verbatim gate has already proven citation integrity, this pass
computes cosine similarities between each item's question + reference answer
and (a) each cited verbatim quote (the sharp, like-for-like comparison) and
(b) each cited chunk (context). Same embedding model as retrieval
(text-embedding-3-large), through the shared $10 budget ledger.

The flag is driven by the quote-level similarity: QA-vs-quote is short-text
vs short-text, so on-topic items land meaningfully above off-topic ones.
QA-vs-chunk runs lower for everyone (a chunk averages many topics), which is
why it is reported for context but does not drive the flag. The default
threshold is provisional, calibrated on the pilot distribution; every raw
value is in the report so the paper can state its own cut.

The verbatim gate stays the only hard filter; this is the fuzzy complement.

Outputs (sidecar; draft datasets stay untouched and hashable):
  evaluation/authoring/similarity/<course_id>.similarity.json
  evaluation/authoring/similarity/summary.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "evaluation" / "runs"
EVIDENCE = ROOT / "evaluation" / "authoring" / "evidence"
OUTDIR = ROOT / "evaluation" / "authoring" / "similarity"
LEDGER = ROOT / "evaluation" / "pilot" / "budget.sqlite3"
PRICES = ROOT / "evaluation" / "prices" / "2026-09-30.openrouter.json"
DEFAULT_THRESHOLD = 0.55
EMBED_BATCH = 64


def _worker_path() -> None:
    sys.path.insert(0, str(ROOT / "apps" / "worker"))
    for name in ("api", "evaluator"):
        conflicting = str(ROOT / "apps" / name)
        while conflicting in sys.path:
            sys.path.remove(conflicting)


def newest_drafts() -> dict[str, Path]:
    """course_id -> newest draft_dataset.json artifact path."""
    drafts: dict[str, Path] = {}
    candidates = sorted(RUNS.glob("*/artifacts/draft_dataset.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        course = payload.get("course_id")
        if isinstance(course, str) and course and course not in drafts:
            drafts[course] = path
    return drafts


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help="flag items whose best quote similarity is below this (default 0.55, provisional)")
    parser.add_argument("--course", help="only score this course id")
    parser.add_argument("--skip-existing", action="store_true", help="leave earlier similarity files alone")
    args = parser.parse_args()

    os.environ["EDUGROUND_EVAL_BUDGET_LEDGER"] = str(LEDGER.resolve())
    os.environ.setdefault("EDUGROUND_EVAL_MAX_API_SPEND_USD", "15")
    os.environ["EDUGROUND_EVAL_PRICE_REGISTRY"] = str(PRICES.resolve())
    _worker_path()
    from app.ledger import INGESTION_EMBEDDING_OPERATION, load_ledger_guard
    from app.services.embeddings import embed_texts
    from app.settings import settings

    drafts = newest_drafts()
    if args.course:
        drafts = {cid: path for cid, path in drafts.items() if cid == args.course}
    if not drafts:
        print("no draft datasets found under evaluation/runs/*/artifacts/draft_dataset.json")
        return 0

    guard = load_ledger_guard()
    if guard is None:
        print("embedding ledger unavailable (EDUGROUND_EVAL_BUDGET_LEDGER)")
        return 2
    guard.schedule(provider="openrouter", model=settings.openrouter_embedding_model,
                   operation=INGESTION_EMBEDDING_OPERATION)
    OUTDIR.mkdir(parents=True, exist_ok=True)

    summary: dict[str, dict] = {}
    for course, draft_path in sorted(drafts.items()):
        out_path = OUTDIR / f"{course}.similarity.json"
        if args.skip_existing and out_path.is_file():
            print(f"{course}: skipped (existing {out_path.name})")
            continue
        packet_path = EVIDENCE / f"{course}.json"
        if not packet_path.is_file():
            print(f"{course}: evidence packet missing, skipped")
            continue
        dataset = json.loads(draft_path.read_text(encoding="utf-8"))
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        chunk_text = {c["chunk_id"]: c["text"] for s in packet["sources"] for c in s["chunks"]}
        samples = dataset.get("samples") or []
        if not samples:
            print(f"{course}: draft has no samples, skipped")
            continue

        qa_texts = [f"{s['question'].strip()}\n{s['reference_answer'].strip()}" for s in samples]
        evidence_per_sample = [list(s.get("evidence") or []) for s in samples]
        needed_chunks = sorted({e["chunk_id"] for group in evidence_per_sample
                                for e in group if e.get("chunk_id") in chunk_text})
        unique_quotes = sorted({(e.get("quote") or "").strip() for group in evidence_per_sample
                                for e in group if (e.get("quote") or "").strip()})
        texts = qa_texts + [chunk_text[cid] for cid in needed_chunks] + unique_quotes

        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            group = texts[start:start + EMBED_BATCH]
            got = embed_texts(group, guard=guard)
            if len(got) != len(group):
                raise RuntimeError(f"{course}: embedding count mismatch {len(got)} != {len(group)}")
            vectors.extend(got)
        qa_vectors = vectors[:len(qa_texts)]
        chunk_vectors = dict(zip(needed_chunks, vectors[len(qa_texts):len(qa_texts) + len(needed_chunks)]))
        quote_vectors = dict(zip(unique_quotes, vectors[len(qa_texts) + len(needed_chunks):]))

        items: list[dict] = []
        for index, sample in enumerate(samples):
            evidence_scores = []
            for entry in evidence_per_sample[index]:
                cid = entry.get("chunk_id")
                quote = (entry.get("quote") or "").strip()
                chunk_vector = chunk_vectors.get(cid)
                quote_vector = quote_vectors.get(quote)
                evidence_scores.append({
                    "chunk_id": cid,
                    "quote_similarity": (round(cosine(qa_vectors[index], quote_vector), 4)
                                         if quote_vector is not None else None),
                    "chunk_similarity": (round(cosine(qa_vectors[index], chunk_vector), 4)
                                         if chunk_vector is not None else None),
                })
            quote_values = [e["quote_similarity"] for e in evidence_scores if e["quote_similarity"] is not None]
            chunk_values = [e["chunk_similarity"] for e in evidence_scores if e["chunk_similarity"] is not None]
            max_quote = max(quote_values) if quote_values else None
            items.append({
                "sample_id": sample["sample_id"],
                "question": sample["question"][:140],
                "split": sample.get("split"),
                "max_quote_similarity": max_quote,
                "mean_quote_similarity": (round(sum(quote_values) / len(quote_values), 4) if quote_values else None),
                "max_chunk_similarity": (max(chunk_values) if chunk_values else None),
                "flagged": bool(max_quote is not None and max_quote < args.threshold),
                "evidence": evidence_scores,
            })
        values = [item["max_quote_similarity"] for item in items if item["max_quote_similarity"] is not None]
        flagged = sum(1 for item in items if item["flagged"])
        report = {
            "course_id": course,
            "dataset_id": dataset.get("dataset_id"),
            "draft_artifact": str(draft_path.relative_to(ROOT)),
            "threshold": args.threshold,
            "embedding_model": settings.openrouter_embedding_model,
            "kind": "soft_similarity_alarm (review cue only; the verbatim gate remains the hard filter)",
            "created_at_utc": datetime.now(UTC).isoformat(),
            "samples": len(items),
            "flagged": flagged,
            "mean_of_max_quote_similarity": round(sum(values) / len(values), 4) if values else None,
            "min_of_max_quote_similarity": round(min(values), 4) if values else None,
            "items": items,
        }
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        summary[course] = {key: report[key] for key in
                           ("samples", "flagged", "mean_of_max_quote_similarity",
                            "min_of_max_quote_similarity", "threshold")}
        flagged_preview = [item["sample_id"] for item in items if item["flagged"]][:5]
        print("%-42s samples=%-3d flagged=%-3d mean_max_quote=%.4f min=%.4f %s" % (
            course, len(items), flagged,
            report["mean_of_max_quote_similarity"] or 0.0, report["min_of_max_quote_similarity"] or 0.0,
            ("| flagged: " + ", ".join(flagged_preview)) if flagged_preview else ""))

    summary_path = OUTDIR / "summary.json"
    existing = {}
    if summary_path.is_file():
        try:
            existing = json.loads(summary_path.read_text(encoding="utf-8")).get("courses", {})
        except ValueError:
            existing = {}
    existing.update(summary)
    totals = {"samples": sum(v["samples"] for v in existing.values()),
              "flagged": sum(v["flagged"] for v in existing.values())}
    summary_path.write_text(json.dumps({
        "kind": "soft_similarity_summary",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "note": "quote-level cosine similarity between each QA pair and its cited verbatim quotes; review cue only",
        "courses": existing,
        "totals": totals,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    state = guard.ledger.state()
    print(f"summary: {len(existing)} course(s) | {totals['flagged']} flagged of {totals['samples']} items "
          f"| ledger settled ${state.settled_usd}")
    print(f"saved: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
