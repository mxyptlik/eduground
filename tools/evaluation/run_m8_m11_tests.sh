#!/usr/bin/env bash
# Run the currently available M8-M11 checks from Git Bash. No model API calls.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONUNBUFFERED=1
PYTHON="${EDUGROUND_PYTHON:-$ROOT/.venv/Scripts/python.exe}"
COURSES="$ROOT/Courses"
API_URL="http://127.0.0.1:8000"
RUN=""
RELEASE=""
CONDITIONS=()
WITH_K6=0
RESULTS_BASE="$ROOT/evaluation/test_runs"

usage() {
  cat <<'EOF'
Usage: bash tools/evaluation/run_m8_m11_tests.sh [options]

  --courses DIR       Course directory (default: Courses/)
  --api-url URL       Running API URL (default: http://127.0.0.1:8000)
  --run DIR           Existing collected evaluation run (for M8/M9 report)
  --release PATH      Frozen release directory or dataset.json; requires --run
  --condition ID      Run condition; repeat for each condition in the report
  --with-k6           Also run bounded k6 read-only health load if installed
  --results DIR       Parent directory for a new timestamped output directory
  --help              Show this help

This runner does not start servers, call model providers, judge answers, or
modify the course files. It continues after a failed check and exits nonzero
if any executed check fails. Missing optional inputs are reported as SKIP.
EOF
}

while (($#)); do
  case "$1" in
    --courses|--api-url|--run|--release|--condition|--results)
      if (($# < 2)); then echo "Missing value for $1" >&2; usage; exit 2; fi
      case "$1" in
        --courses) COURSES="$2" ;;
        --api-url) API_URL="$2" ;;
        --run) RUN="$2" ;;
        --release) RELEASE="$2" ;;
        --condition) CONDITIONS+=("$2") ;;
        --results) RESULTS_BASE="$2" ;;
      esac
      shift 2 ;;
    --with-k6) WITH_K6=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ ! -f "$PYTHON" ]]; then
  echo "Python not found: $PYTHON (set EDUGROUND_PYTHON to override)" >&2
  exit 2
fi
if [[ -n "$RUN" || -n "$RELEASE" || ${#CONDITIONS[@]} -gt 0 ]]; then
  if [[ -z "$RUN" || -z "$RELEASE" || ${#CONDITIONS[@]} -eq 0 ]]; then
    echo "--run, --release, and at least one --condition must be supplied together" >&2
    exit 2
  fi
fi

mkdir -p "$RESULTS_BASE" || exit 2
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$RESULTS_BASE/m8-m11-$STAMP-$$"
mkdir "$OUT" || exit 2
mkdir "$OUT/logs" || exit 2
SUMMARY="$OUT/summary.tsv"
printf 'module\tcheck\tstatus\tdetail\tlog\n' > "$SUMMARY"
FAILURES=0
cd "$ROOT" || exit 2

record() {
  local module="$1" check="$2" status="$3" detail="$4" log="${5:-}"
  detail="${detail//$'\t'/ }"
  detail="${detail//$'\n'/ }"
  printf '%s\t%s\t%s\t%s\t%s\n' "$module" "$check" "$status" "$detail" "$log" | tee -a "$SUMMARY"
}

run_check() {
  local module="$1" check="$2"
  shift 2
  local log="$OUT/logs/$check.log" code
  echo
  echo "[$module] $check"
  "$@" 2>&1 | tee "$log"
  code=${PIPESTATUS[0]}
  if ((code == 0)); then
    record "$module" "$check" PASS "exit 0" "$log"
  else
    record "$module" "$check" FAIL "exit $code" "$log"
    FAILURES=$((FAILURES + 1))
  fi
}

finish() {
  local code=$?
  echo
  echo "Results: $OUT"
  echo "Summary: $SUMMARY"
  echo "Failed checks: $FAILURES"
  if ((code != 0 || FAILURES > 0)); then exit 1; fi
}
trap finish EXIT

run_check M8 telemetry_unit "$PYTHON" -m pytest -q \
  evaluation/program/tests/test_usage.py \
  evaluation/program/tests/test_timing.py \
  evaluation/program/tests/test_llm_telemetry.py \
  evaluation/program/tests/test_reranker_telemetry.py \
  evaluation/program/tests/test_runner.py \
  evaluation/program/tests/test_run_report.py
run_check M8 worker_telemetry_unit "$PYTHON" -m pytest -q \
  apps/worker/tests/unit/test_telemetry.py

run_check M9 cost_budget_unit "$PYTHON" -m pytest -q \
  evaluation/program/tests/test_pricing.py \
  evaluation/program/tests/test_cost_reports.py \
  evaluation/program/tests/test_budget.py \
  evaluation/program/tests/test_budgeted_call.py \
  evaluation/program/tests/test_budgeted_embeddings.py \
  evaluation/program/tests/test_budgeted_rerank.py \
  evaluation/program/tests/test_budgeted_transport.py
run_check M9 worker_cost_guard_unit "$PYTHON" -m pytest -q \
  apps/worker/tests/unit/test_ledger.py \
  apps/worker/tests/unit/test_ledger_compat.py \
  apps/worker/tests/unit/test_guarded_ingestion.py

if [[ -n "$RUN" ]]; then
  REPORT_ARGS=(--run "$RUN" --release "$RELEASE")
  for condition in "${CONDITIONS[@]}"; do REPORT_ARGS+=(--condition "$condition"); done
  run_check M8-M9 collected_run_report "$PYTHON" tools/evaluation/run_report.py "${REPORT_ARGS[@]}"
else
  record M8-M9 collected_run_report SKIP "needs an existing collected run, frozen release, and condition IDs"
fi
record M8 end_to_end_trace_and_ttft SKIP "requires instrumented live answer/ingestion paths and true streaming"
record M9 provider_invoice_reconciliation SKIP "requires dated prices, actual usage, and provider billing records"
record M9 scenario_cost_projection SKIP "requires declared user/course volume assumptions"

# Check the API before generating any load. The health path does not use a model.
if "$PYTHON" -c 'import json, sys, urllib.request; base=sys.argv[1].rstrip("/"); spec=json.load(urllib.request.urlopen(base+"/openapi.json", timeout=2)); health=json.load(urllib.request.urlopen(base+"/health", timeout=2)); assert spec.get("info", {}).get("title") == "Curriculum Tutor API" and health.get("status") == "ok"' "$API_URL" >/dev/null 2>&1; then
  run_check M10 health_concurrency_smoke "$PYTHON" tools/evaluation/m8_m11_checks.py health \
    --api-url "$API_URL" --output "$OUT/health_load.json"
  if ((WITH_K6)); then
    if command -v k6 >/dev/null 2>&1; then
      run_check M10 k6_read_health env K6_API_BASE_URL="$API_URL" K6_VUS=5 K6_DURATION=30s \
        K6_BEARER_TOKEN= K6_NOTEBOOK_ID= k6 run --summary-export "$OUT/k6_read_health.json" \
        tests/system/load/read_path.js
    else
      record M10 k6_read_health SKIP "k6 is not installed"
    fi
  else
    record M10 k6_read_health SKIP "opt in with --with-k6"
  fi
else
  record M10 health_concurrency_smoke SKIP "Eduground API identity or /health unavailable at $API_URL"
  record M10 k6_read_health SKIP "Eduground API identity or /health unavailable at $API_URL"
fi
record M10 authenticated_multi_user_study SKIP "needs distinct test users/notebooks and a workload protocol"
record M10 real_provider_chat_load SKIP "needs explicit cost-bounded protocol and a shared live ledger"
run_check M10-M11 terminal_tools_unit "$PYTHON" -m pytest -q \
  evaluation/program/tests/test_terminal_m10_m11_tools.py

run_check M11 format_unit "$PYTHON" -m pytest -q \
  evaluation/program/tests/test_format_matrix.py
run_check M11 worker_parser_unit "$PYTHON" -m pytest -q \
  apps/worker/tests/unit/test_parsing.py \
  apps/worker/tests/unit/test_visual_layer.py \
  apps/worker/tests/unit/test_ocr.py
if [[ -d "$COURSES" ]]; then
  run_check M11 current_corpus_formats "$PYTHON" tools/evaluation/m8_m11_checks.py formats \
    --courses "$COURSES" --output "$OUT/formats.json"
else
  record M11 current_corpus_formats SKIP "course directory is missing: $COURSES"
fi
record M11 extraction_accuracy_study SKIP "needs human transcripts and structure labels"
record M11 seventeen_course_generalisation SKIP "needs frozen per-course evaluation runs"

if ((FAILURES > 0)); then exit 1; fi
