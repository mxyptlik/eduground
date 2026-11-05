#!/usr/bin/env bash
# Two-phase authoring driver.
#
#   PHASE 1 — first pass over all courses: one attempt per course (resume its
#   journal when one exists, else start fresh). Completed batches are
#   journalled; a failure is noted and does NOT block the other courses.
#
#   PHASE 2 — convergence sweep: rounds of retries over the unfinished
#   courses only, until everything completes or MAX_ROUNDS is reached. Each
#   attempt resumes the course's journal, so only failed/running batches are
#   ever re-run — completed batches (and their spend) are never repeated.
#
# Parallel courses within both phases; Ctrl+C is safe (journals resume).
#
# Usage (Git Bash):
#   bash tools/evaluation/run_authoring_all.sh
#   PARALLEL=4 MAX_ROUNDS=8 bash tools/evaluation/run_authoring_all.sh
# Live dashboard (second terminal):
#   .venv/Scripts/python.exe tools/evaluation/authoring_progress.py --watch

set -u
cd "$(dirname "$0")/../.." || exit 1

PY=".venv/Scripts/python.exe"
PROGRESS="tools/evaluation/authoring_progress.py"
MAX_ROUNDS="${MAX_ROUNDS:-6}"
PARALLEL="${PARALLEL:-3}"

export PYTHONUNBUFFERED=1
export EDUGROUND_EVAL_MAX_API_SPEND_USD="${EDUGROUND_EVAL_MAX_API_SPEND_USD:-15}"
mkdir -p evaluation/runs

# Captured python output must be freed of Windows CR characters or course
# ids become "C\r" and paths break with EINVAL.
strip_cr() { tr -d '\r'; }

attempt_course() {
  local cid="$1"
  local log="evaluation/runs/authoring-$cid.log"
  local resume
  resume=$("$PY" "$PROGRESS" --resume-dir-for "$cid" | strip_cr)
  local args=(tools/evaluation/run_authoring.py
    --evidence-packet "evaluation/authoring/evidence/$cid.json"
    --blueprint "evaluation/authoring/blueprints/$cid.blueprint.json"
    --run-root evaluation/runs
    --execute
    --prices evaluation/prices/2026-09-30.openrouter.json
    --ledger evaluation/pilot/budget.sqlite3
    --dataset-id "$cid"
    --version draft-1
    --retry-failed
    --recover-interrupted)
  local label="fresh"
  if [ -n "$resume" ]; then
    args+=(--resume "$resume")
    label="resume $resume"
  fi
  echo "=== $cid ($label) ==="
  "$PY" "${args[@]}" 2>&1 | tee -a "$log"
  return ${PIPESTATUS[0]}
}

run_pool() {
  # Run attempt_course for every given course id, at most PARALLEL at once.
  local cid
  for cid in $1; do
    while [ "$(jobs -rp | wc -l)" -ge "$PARALLEL" ]; do
      sleep 3
    done
    (
      if attempt_course "$cid"; then
        echo "OK: $cid"
      else
        echo "PENDING: $cid"
      fi
    ) &
  done
  wait
}

echo "Authoring driver (two-phase) | cap \$$EDUGROUND_EVAL_MAX_API_SPEND_USD | parallel: $PARALLEL | rounds: $MAX_ROUNDS"
echo "Per-course logs: evaluation/runs/authoring-<course>.log"
echo "Live dashboard:  $PY $PROGRESS --watch"

echo
"$PY" "$PROGRESS" --reconcile
echo "########## PHASE 1 — first pass over all courses ##########"
run_pool "$("$PY" "$PROGRESS" --list-pending | strip_cr)"

round=1
while [ "$round" -le "$MAX_ROUNDS" ]; do
  "$PY" "$PROGRESS" --reconcile
  pending=$("$PY" "$PROGRESS" --list-pending | strip_cr)
  if [ -z "$pending" ]; then
    echo
    echo "All courses complete — no convergence sweep needed."
    break
  fi
  echo
  echo "########## PHASE 2 — convergence round $round ##########"
  run_pool "$pending"
  round=$((round + 1))
done

echo
echo "=== SUMMARY ==="
"$PY" "$PROGRESS"
