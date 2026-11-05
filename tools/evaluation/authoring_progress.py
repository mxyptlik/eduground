"""Live view of authoring progress across journaled runs. No provider calls.

Reads evaluation/runs/*/journal.sqlite3 and the shared pilot ledger to answer:
which course is done, which batch failed, how many questions exist, what was
spent. Safe to run while authoring runs (read-only, short sqlite timeouts).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "evaluation" / "runs"
EVIDENCE = ROOT / "evaluation" / "authoring" / "evidence"
LEDGER = ROOT / "evaluation" / "pilot" / "budget.sqlite3"
BATCHES_PER_COURSE = 3


def list_courses() -> list[str]:
    return sorted(path.stem for path in EVIDENCE.glob("*.json"))


def journals() -> list[Path]:
    candidates = list(RUNS.glob("*/journal.sqlite3"))
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)


def _rows(journal: Path):
    try:
        db = sqlite3.connect(journal, timeout=10)
        return db.execute("SELECT item_id, status, result_json FROM steps").fetchall()
    except Exception:
        return []


def scan() -> dict[str, dict]:
    courses = list_courses()
    data = {cid: {"completed": set(), "failed": set(), "running": set(), "q_by_item": {}} for cid in courses}
    for journal in journals():
        for item_id, status, result_json in _rows(journal):
            cid = next((c for c in courses if item_id and item_id.startswith(f"{c}-")), None)
            if cid is None:
                continue
            entry = data[cid]
            if status == "completed":
                entry["completed"].add(item_id)
                count = 0
                if result_json:
                    try:
                        count = len(json.loads(result_json).get("questions", []))
                    except Exception:
                        count = entry["q_by_item"].get(item_id, 0)
                entry["q_by_item"][item_id] = max(entry["q_by_item"].get(item_id, 0), count)
            elif status == "failed":
                entry["failed"].add(item_id)
            else:
                entry["running"].add(item_id)
    return data


def ledger_line() -> str:
    try:
        db = sqlite3.connect(LEDGER, timeout=10)
        settled = db.execute("SELECT COALESCE(SUM(CAST(actual_usd AS REAL)),0) FROM calls WHERE actual_usd IS NOT NULL").fetchone()[0]
        reserved = db.execute("SELECT COALESCE(SUM(CAST(reserved_usd AS REAL)),0) FROM calls WHERE status='reserved'").fetchone()[0]
        unknown = db.execute("SELECT count(*) FROM calls WHERE status='unknown'").fetchone()[0]
        return "ledger: settled $%.4f | reserved $%.4f | unknown %d" % (settled, reserved, unknown)
    except Exception as exc:  # pragma: no cover - read-only helper
        return f"ledger unavailable: {exc}"


def dashboard() -> None:
    data = scan()
    done_courses = 0
    total_questions = 0
    print("course                                         done failed run   questions")
    print("-" * 78)
    for cid, entry in data.items():
        done = len(entry["completed"])
        if done >= BATCHES_PER_COURSE:
            done_courses += 1
        questions = sum(entry["q_by_item"].values())
        total_questions += questions
        print("%-42s %4d %6d %4d %10d" % (cid[:42], done, len(entry["failed"]), len(entry["running"]), questions))
    print("-" * 78)
    print("courses complete: %d/%d | distinct questions authored: %d" % (done_courses, len(data), total_questions))
    print(ledger_line())


def is_done(cid: str) -> bool:
    return len(scan().get(cid, {}).get("completed", ())) >= BATCHES_PER_COURSE


def latest_run_dir() -> str:
    found = journals()
    return found[0].parent.name if found else ""


def resume_dir_for(cid: str) -> str:
    for journal in journals():
        for item_id, _status, _result in _rows(journal):
            if item_id and item_id.startswith(f"{cid}-"):
                return journal.parent.name
    return ""


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            # newline="\n" keeps piped output LF-only; Git Bash command
            # substitution otherwise captures a trailing CR on Windows.
            sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
        except (TypeError, ValueError):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true", help="refresh the dashboard every --interval seconds")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--list-courses", action="store_true")
    parser.add_argument("--list-pending", action="store_true", help="courses whose 3 batches are not all completed")
    parser.add_argument("--reconcile", action="store_true",
                        help="settle stale un-settled calls at their reserved bound (safe only when no writer runs)")
    parser.add_argument("--is-done", metavar="COURSE_ID")
    parser.add_argument("--latest-run-dir", action="store_true")
    parser.add_argument("--resume-dir-for", metavar="COURSE_ID")
    args = parser.parse_args()

    if args.list_courses:
        print("\n".join(list_courses()))
        return 0
    if args.is_done:
        return 0 if is_done(args.is_done) else 1
    if args.list_pending:
        data = scan()
        print("\n".join(cid for cid, entry in data.items()
                        if len(entry["completed"]) < BATCHES_PER_COURSE))
        return 0
    if args.reconcile:
        # Safe at driver boundaries: every non-settled row then belongs to an
        # interrupted process. Settle conservatively at the reserved bound so
        # the shared ledger stops blocking new calls.
        sys.path.insert(0, str(ROOT))
        from evaluation.program.budget import BudgetLedger

        rows = sqlite3.connect(LEDGER).execute(
            "SELECT call_id, reserved_usd FROM calls WHERE status != 'settled'").fetchall()
        ledger = BudgetLedger(LEDGER, os.environ.get("EDUGROUND_EVAL_MAX_API_SPEND_USD", "15"))
        for call_id, reserved in rows:
            ledger.settle(call_id, reserved)
        print(f"ledger reconciled: {len(rows)} stale call(s) settled at their reserved bound")
        return 0
    if args.latest_run_dir:
        print(latest_run_dir())
        return 0
    if args.resume_dir_for:
        print(resume_dir_for(args.resume_dir_for))
        return 0
    if args.watch:
        while True:
            os.system("cls" if os.name == "nt" else "clear")
            dashboard()
            print("\n(watching — Ctrl+C to stop; the run itself keeps going)")
            time.sleep(max(1.0, args.interval))
    dashboard()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
