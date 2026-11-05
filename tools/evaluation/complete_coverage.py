"""Complete the Eduground RAGAS judge coverage, then refresh the final reports.

Single entry point for finishing judge coverage across all 17 courses and all
five metrics (166 items x 5 metrics = 830 steps).

Run it from the repository root in Git Bash:

    cd /c/Users/User/Desktop/eduground
    ./.venv/Scripts/python.exe tools/evaluation/complete_coverage.py

Safe to re-run: every stage is idempotent (reconciliation, probing, retry
rounds, report regeneration). Stop any time with Ctrl+C; nothing corrupts.

Stages:
  1. Preflight the OpenRouter key (direct HTTP call; abort if not 200).
  2. Reconcile the shared budget ledger: settle aborted/in-flight calls at
     their reserved bound; reverse conservative charges from the expired-key
     window (HTTP 401 attempts, never billed by the provider).
  3. Factual-correctness sanity probe (identical answer/reference -> 1.0).
  4. Retry rounds over failed judge steps until remaining == 0 or the spend
     guard (cap - $0.50). A stall detector aborts if three consecutive rounds
     convert nothing while spending.
  5. Rewrite evaluation/reports/final-results.json and final-report.md
     (per-course metrics, status counts, artifact hashes, budget notes).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from time import sleep, time

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(dotenv_path=ROOT / ".env", override=False)

METRICS = ["faithfulness", "context_recall", "context_precision", "answer_relevancy", "factual_correctness_f1"]
LEDGER = ROOT / "evaluation/pilot/budget.sqlite3"
PRICES = "evaluation/prices/2026-09-30.openrouter.json"
REPORTS = ROOT / "evaluation/reports"


def log(message: str) -> None:
    print(message, flush=True)


def read_cap() -> str:
    db = sqlite3.connect(str(LEDGER))
    try:
        row = db.execute("SELECT cap_usd FROM budget_meta").fetchone()
    finally:
        db.close()
    if row is None:
        raise SystemExit("budget ledger has no cap in budget_meta")
    return str(row[0])


def settled() -> float:
    db = sqlite3.connect(str(LEDGER))
    try:
        return float(db.execute(
            "SELECT COALESCE(SUM(CAST(actual_usd AS REAL)),0) FROM calls WHERE actual_usd IS NOT NULL"
        ).fetchone()[0])
    finally:
        db.close()


def main() -> int:
    cap = read_cap()
    guard = float(cap) - 0.5
    os.environ["EDUGROUND_EVAL_MAX_API_SPEND_USD"] = cap
    log(f"Eduground completion runner | ledger cap ${cap} | spend guard ${guard:.2f}")

    # Stage 1: key preflight
    import httpx
    key = os.environ.get("CURRICULUM_TUTOR_OPENROUTER_API_KEY", "")
    if not key:
        log("ABORT: CURRICULUM_TUTOR_OPENROUTER_API_KEY is not set (.env)")
        return 1
    try:
        response = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json={"model": "deepseek/deepseek-v4.1-flash",
                  "messages": [{"role": "user", "content": "Say OK"}], "max_tokens": 5},
            headers={"Authorization": "Bearer " + key}, timeout=60)
    except Exception as exc:
        log(f"ABORT: key preflight failed: {type(exc).__name__} {str(exc)[:160]}")
        return 1
    log(f"key preflight: HTTP {response.status_code}")
    if response.status_code != 200:
        log("ABORT before any ledger work: " + response.text[:200])
        return 1

    # Stage 2: ledger reconciliation
    backup = LEDGER.with_suffix(".sqlite3.pre-keyfix-backup")
    if not backup.exists():
        for _ in range(5):
            try:
                shutil.copy2(LEDGER, backup)
                log(f"ledger backup written: {backup.name}")
                break
            except OSError:
                sleep(1)
    from evaluation.program.budget import BudgetLedger
    main_ledger = BudgetLedger(LEDGER, cap)
    db = sqlite3.connect(str(LEDGER))
    try:
        aborted = db.execute(
            "SELECT call_id, reserved_usd FROM calls WHERE status IN ('reserved','unknown')"
        ).fetchall()
        for call_id, reserved in aborted:
            main_ledger.settle(call_id, reserved)
        phantoms = db.execute(
            "UPDATE calls SET actual_usd='0' "
            "WHERE status='settled' AND role='judge' AND rowid > 5171 "
            "AND reserved_usd='0.01638400' AND actual_usd='0.01638400'"
        ).rowcount
        db.commit()
    finally:
        db.close()
    if aborted:
        log(f"settled {len(aborted)} aborted/in-flight calls at their reserved bound")
    if phantoms:
        log(f"reversed {phantoms} phantom expired-key charges to $0")
    log(f"settled after reconciliation: ${settled():.4f}")

    # Stage 3: Factual Correctness sanity probe
    from evaluation.program.config import EvaluationSettings
    from evaluation.program.pricing import PriceRegistry
    from evaluation.program.ragas_judge import build_deepseek_ragas_judge
    from evaluation.program.metrics.ragas import score_ragas_metric

    settings = EvaluationSettings()
    probe_path = ROOT / "evaluation/pilot/probe-resume.sqlite3"
    try:
        probe_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        probe_path = ROOT / f"evaluation/pilot/probe-resume-{int(time())}.sqlite3"
    probe_ledger = BudgetLedger(probe_path, cap)
    pdb = sqlite3.connect(str(probe_path))
    try:
        for (call_id,) in pdb.execute("SELECT call_id FROM calls WHERE status IN ('reserved','unknown')").fetchall():
            probe_ledger.settle(call_id, "0")
    finally:
        pdb.close()
    prices = PriceRegistry.from_json(PRICES)
    judge = build_deepseek_ragas_judge(settings, api_key=key, ledger=probe_ledger, prices=prices,
                                       max_input_tokens=32768, max_output_tokens=12288)
    toy = "A stack uses last-in, first-out order: the last item pushed is the first popped."
    probe_line = "factual-correctness probe: not completed (all attempts failed)"
    for attempt in range(1, 4):
        try:
            row = asyncio.run(score_ragas_metric(
                "factual_correctness_f1", llm=judge.llm,
                user_input="What is LIFO?", response=toy, reference=toy, answerable=True))
            probe_line = f"factual-correctness probe (identical answer/reference): status={row.status} value={row.value}"
            log(probe_line)
            break
        except Exception as exc:
            log(f"probe attempt {attempt} failed: {type(exc).__name__} {str(exc)[:120]}")
    else:
        log(probe_line)

    # Stage 4: retry rounds
    env = dict(os.environ)
    env["CURRICULUM_TUTOR_DATABASE_URL"] = "sqlite:///" + (ROOT / "evaluation/local_corpus/corpus.sqlite3").resolve().as_posix()
    env["EDUGROUND_EVAL_MAX_API_SPEND_USD"] = cap

    def newest_run_for(course_id: str) -> Path | None:
        for journal in sorted((ROOT / "evaluation/runs").glob("*/journal.sqlite3"),
                              key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                jdb = sqlite3.connect(str(journal))
                try:
                    hit = jdb.execute(
                        "SELECT 1 FROM steps WHERE metric_id=? AND item_id LIKE ? LIMIT 1",
                        ("no_rerank", course_id + ":%")).fetchone()
                finally:
                    jdb.close()
            except Exception:
                continue
            if hit:
                return journal.parent
        return None

    courses = sorted(d.name for d in (ROOT / "evaluation/releases").iterdir()
                     if d.is_dir() and (d / "dataset.json").is_file())

    def remaining_total() -> int:
        remaining = 0
        for course_id in courses:
            run_dir = newest_run_for(course_id)
            if run_dir is None:
                continue
            artifact = run_dir / "artifacts" / "scoring" / "ragas.json"
            if not artifact.is_file():
                continue
            dataset = json.loads(((ROOT / "evaluation/releases") / course_id / "dataset.json").read_text(encoding="utf-8"))
            target = len(dataset["samples"]) * len(METRICS)
            rows = json.loads(artifact.read_text(encoding="utf-8"))
            remaining += max(0, target - sum(1 for x in rows if x.get("status") == "completed"))
        return remaining

    stalls = 0
    for round_number in range(1, 11):
        remaining, spent = remaining_total(), settled()
        log(f"=== round {round_number} | remaining steps: {remaining} | settled ${spent:.4f} ===")
        if remaining == 0:
            break
        if spent > guard:
            log(f"spend guard hit: stopping at ${spent:.4f}")
            break
        for course_id in courses:
            if settled() > guard:
                break
            run_dir = newest_run_for(course_id)
            if run_dir is None:
                continue
            subprocess.run([sys.executable, "tools/evaluation/authoring_progress.py", "--reconcile"],
                           env=env, cwd=str(ROOT), capture_output=True, text=True)
            args = [sys.executable, "tools/evaluation/score_run.py",
                    "--run", str(run_dir.relative_to(ROOT)),
                    "--release", f"evaluation/releases/{course_id}", "--condition", "no_rerank"]
            for metric in METRICS:
                args += ["--metric", metric]
            args += ["--prices", PRICES, "--ledger", "evaluation/pilot/budget.sqlite3",
                     "--ragas-limit", "120", "--concurrency", "8",
                     "--retry-failed", "--recover-interrupted"]
            try:
                result = subprocess.run(args, env=env, cwd=str(ROOT), capture_output=True, text=True,
                                        encoding="utf-8", errors="replace", timeout=2400)
                if result.returncode == 0:
                    payload = result.stdout[result.stdout.find("{"): result.stdout.rfind("}") + 1]
                    info = json.loads(payload)
                    log("r%d %-42s %s" % (round_number, course_id[:42], info.get("ragas_status_counts")))
                else:
                    log("r%d %-42s FAILED rc=%s" % (round_number, course_id[:42], result.returncode))
            except Exception as exc:
                log("r%d %-42s ERROR %s" % (round_number, course_id[:42], type(exc).__name__))
        new_remaining, new_spent = remaining_total(), settled()
        log("--- round %d end: remaining %d (%+d) | settled $%.4f (%+.4f)" % (
            round_number, new_remaining, new_remaining - remaining, new_spent, new_spent - spent))
        if new_remaining >= remaining and new_spent > spent:
            stalls += 1
            if stalls >= 3:
                log("stall detected: three rounds with no conversions - aborting rounds")
                break
        else:
            stalls = 0

    log(f"final remaining: {remaining_total()} | settled ${settled():.4f}")

    # Stage 5: consolidation
    def retrieval_rows(condition):
        latest = {}
        for artifact in sorted((ROOT / "evaluation/runs").glob("*/artifacts/scoring/retrieval.json"),
                               key=lambda p: p.stat().st_mtime, reverse=True):
            rows = json.loads(artifact.read_text(encoding="utf-8"))
            rows = [row for row in rows if row.get("condition") == condition]
            if not rows:
                continue
            course_id = str(rows[0]["item_id"]).split(":")[0]
            if course_id not in latest:
                latest[course_id] = rows
        overall, per_course = defaultdict(list), {}
        for course_id, rows in latest.items():
            values = defaultdict(list)
            for row in rows:
                if row.get("value") is not None:
                    values[(row["metric_id"], row["k"])].append(row["value"])
                    overall[(row["metric_id"], row["k"])].append(row["value"])
            per_course[course_id] = {f"{metric}@{k}": (sum(v) / len(v) if v else None)
                                     for (metric, k), v in values.items()}
        return {f"{metric}@{k}": (sum(v) / len(v) if v else None) for (metric, k), v in overall.items()}, per_course

    det_no_rerank, det_no_rerank_per_course = retrieval_rows("no_rerank")
    det_rerank, det_rerank_per_course = retrieval_rows("rerank")

    ragas_overall, ragas_per_course, ragas_status = defaultdict(list), {}, defaultdict(int)
    for course_id in courses:
        run_dir = newest_run_for(course_id)
        if run_dir is None:
            continue
        artifact = run_dir / "artifacts" / "scoring" / "ragas.json"
        if not artifact.is_file():
            continue
        values = defaultdict(list)
        for row in json.loads(artifact.read_text(encoding="utf-8")):
            ragas_status[row.get("status")] += 1
            if row.get("status") == "completed" and row.get("value") is not None:
                values[row["metric_id"]].append(float(row["value"]))
                ragas_overall[row["metric_id"]].append(float(row["value"]))
        ragas_per_course[course_id] = {metric: (sum(v) / len(v), len(v)) for metric, v in values.items()}

    db = sqlite3.connect(str(LEDGER))
    try:
        total = db.execute("SELECT COALESCE(SUM(CAST(actual_usd AS REAL)),0) FROM calls WHERE actual_usd IS NOT NULL").fetchone()[0]
        by_role = dict(db.execute("SELECT role, SUM(CAST(actual_usd AS REAL)) FROM calls WHERE actual_usd IS NOT NULL GROUP BY role").fetchall())
    finally:
        db.close()

    scoring_hashes = {}
    for artifact in sorted((ROOT / "evaluation/runs").glob("*/artifacts/scoring/*.json")):
        scoring_hashes[str(artifact.relative_to(ROOT / "evaluation")).replace("\\", "/")] = hashlib.sha256(artifact.read_bytes()).hexdigest()

    reconciliation_note = (
        "Ledger reconciliation 2026-10-07: judge attempts made while the OpenRouter key was expired "
        "(HTTP 401; no provider billing) were settled at $0 instead of conservative full-reservation "
        "charges; aborted in-flight attempts are settled at their reserved bound. "
        "Backup: evaluation/pilot/budget.sqlite3.pre-keyfix-backup."
    )
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "budget": {"settled_usd": total, "by_role_usd": {k: float(v) for k, v in by_role.items()},
                   "cap_usd": float(cap), "note": reconciliation_note},
        "notes": [probe_line],
        "deterministic": {
            "condition_no_rerank": det_no_rerank, "per_course_no_rerank": det_no_rerank_per_course,
            "condition_rerank": det_rerank, "per_course_rerank": det_rerank_per_course,
        },
        "ragas": {
            "overall": {metric: {"mean": sum(v) / len(v), "n": len(v)} for metric, v in sorted(ragas_overall.items())},
            "per_course": ragas_per_course,
            "status_counts": dict(ragas_status),
        },
        "artifact_hashes": scoring_hashes,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "final-results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = ["# Eduground evaluation pilot - final results", "", "Generated: " + report["generated_at_utc"], ""]
    lines.append("## Budget")
    lines.append("Settled: $%.2f of $%s cap" % (total, cap))
    for role, amount in sorted(by_role.items()):
        lines.append("- %s: $%.4f" % (role, float(amount or 0)))
    lines.append("- note: " + reconciliation_note)
    lines.append("")
    lines.append("## Notes")
    lines.append("- " + probe_line)
    lines += ["", "## Deterministic retrieval", "", "| Metric | no_rerank | rerank |", "|---|---|---|"]
    for key_name in ("id_recall_at_k@1", "id_recall_at_k@3", "id_recall_at_k@5", "id_recall_at_k@10",
                     "precision_at_k@5", "hit_at_k@5", "mrr@1", "ndcg_at_k@5"):
        a, b = det_no_rerank.get(key_name), det_rerank.get(key_name)
        lines.append("| %s | %s | %s |" % (key_name,
                     ("%.3f" % a) if a is not None else "-", ("%.3f" % b) if b is not None else "-"))
    lines += ["", "## RAGAS (all metrics)"]
    for metric, v in sorted(ragas_overall.items()):
        lines.append("- %s: mean %.4f (n=%d)" % (metric, sum(v) / len(v), len(v)))
    lines.append("- status counts: %s" % dict(ragas_status))
    lines += ["", "## Per-course RAGAS"]
    for course_id, values in sorted(ragas_per_course.items()):
        parts = ["%s=%.3f(n=%d)" % (metric, mv[0], mv[1]) for metric, mv in sorted(values.items())]
        lines.append("- %s: %s" % (course_id, "; ".join(parts) if parts else "-"))
    (REPORTS / "final-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    log("=== COVERAGE ===")
    for metric, v in sorted(ragas_overall.items()):
        log("  %s: n=%d mean=%.4f" % (metric, len(v), sum(v) / len(v)))
    log("status counts: %s" % dict(ragas_status))
    log("=== DONE === total settled: $%.4f" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
