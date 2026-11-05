"""Export completed evaluation results for human review.

Reads, per course, the newest no_rerank run (answers.jsonl + ragas.json) and
the frozen release (question, reference), then writes
tools/evaluation/review/evals.json for the local evaluation-review page
(tools/evaluation/review/evals.html).

Run from the repository root:

    ./.venv/Scripts/python.exe tools/evaluation/review/export_eval_review.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "evals.json"
METRICS = [
    "faithfulness",
    "context_recall",
    "context_precision",
    "answer_relevancy",
    "factual_correctness_f1",
]


def newest_run_for(course_id: str):
    for journal in sorted((ROOT / "evaluation/runs").glob("*/journal.sqlite3"),
                          key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            db = sqlite3.connect(str(journal))
            try:
                hit = db.execute(
                    "SELECT 1 FROM steps WHERE metric_id=? AND item_id LIKE ? LIMIT 1",
                    ("no_rerank", course_id + ":%")).fetchone()
            finally:
                db.close()
        except Exception:
            continue
        if hit:
            return journal.parent
    return None


def main() -> int:
    courses = sorted(d.name for d in (ROOT / "evaluation/releases").iterdir()
                     if d.is_dir() and (d / "dataset.json").is_file())
    out = {"generated_at_utc": datetime.now(UTC).isoformat(), "courses": []}
    total_items = 0
    for course_id in courses:
        dataset = json.loads((ROOT / "evaluation/releases" / course_id / "dataset.json").read_text(encoding="utf-8"))
        run_dir = newest_run_for(course_id)
        answers: dict[str, str] = {}
        metrics: dict[str, dict[str, float | None]] = {}
        if run_dir is not None:
            answers_path = run_dir / "artifacts" / "answers.jsonl"
            if answers_path.is_file():
                preferred: dict[str, str] = {}
                fallback: dict[str, str] = {}
                for line in answers_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    target = preferred if record.get("condition") == "no_rerank" else fallback
                    target[record["item_id"]] = record.get("answer_text", "")
                answers = fallback
                answers.update(preferred)
            ragas_path = run_dir / "artifacts" / "scoring" / "ragas.json"
            if ragas_path.is_file():
                for row in json.loads(ragas_path.read_text(encoding="utf-8")):
                    bucket = metrics.setdefault(row["item_id"], {})
                    bucket[row["metric_id"]] = (row.get("value")
                                                if row.get("status") == "completed" else None)
        items = []
        for sample in dataset["samples"]:
            key = course_id + ":" + sample["sample_id"]
            items.append({
                "item_id": key,
                "question": sample["question"],
                "reference": sample["reference_answer"],
                "question_type": sample.get("question_type"),
                "difficulty": sample.get("difficulty"),
                "answer": answers.get(key),
                "metrics": {metric: (metrics.get(key) or {}).get(metric) for metric in METRICS},
            })
        total_items += len(items)
        out["courses"].append({
            "course_id": course_id,
            "run_dir": str(run_dir.relative_to(ROOT)) if run_dir else None,
            "items": items,
        })
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote %s | courses: %d | items: %d | bytes: %d"
          % (OUT, len(out["courses"]), total_items, OUT.stat().st_size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
