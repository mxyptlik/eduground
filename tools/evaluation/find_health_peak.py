"""Five-minute, bounded binary search of Eduground's unauthenticated health path.

This measures concurrent k6 virtual users on /health, not distinct students or
chat capacity. It never sends a model request or an authentication token.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import threading
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tests" / "system" / "load" / "read_path.js"


def save(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def preflight(url: str) -> None:
    with urlopen(url.rstrip("/") + "/openapi.json", timeout=3) as response:
        spec = json.load(response)
    with urlopen(url.rstrip("/") + "/health", timeout=3) as response:
        health = json.load(response)
    if spec.get("info", {}).get("title") != "Curriculum Tutor API" or health.get("status") != "ok":
        raise ValueError("URL does not identify a healthy Eduground API")


def evaluate(metrics: dict, vus: int, seconds: int, args: argparse.Namespace) -> tuple[str, list[str], dict]:
    latency = metrics.get("http_req_duration") or {}
    failures = metrics.get("http_req_failed") or {}
    checks = metrics.get("checks") or {}
    requests = metrics.get("http_reqs") or {}
    active = metrics.get("vus") or {}
    observed = {
        "p95_ms": latency.get("p(95)"),
        "failure_rate": failures.get("value"),
        "check_rate": checks.get("value"),
        "request_count": requests.get("count"),
        "request_rate": requests.get("rate"),
        "max_active_vus": active.get("max"),
    }
    if any(not isinstance(observed[key], (int, float)) for key in
           ("p95_ms", "failure_rate", "check_rate", "request_count", "max_active_vus")):
        return "invalid", ["k6 summary lacks required measurements"], observed
    inconclusive = []
    minimum_requests = int(vus * seconds * args.min_requests_per_vu_second)
    if observed["max_active_vus"] < vus:
        inconclusive.append("requested_vus_not_reached")
    if observed["request_count"] < minimum_requests:
        inconclusive.append(f"request_count_below_{minimum_requests}")
    if inconclusive:
        return "invalid", inconclusive, observed
    reasons = []
    if observed["p95_ms"] >= args.p95_ms:
        reasons.append("p95_limit")
    if observed["failure_rate"] >= args.max_failure_rate:
        reasons.append("failure_rate_limit")
    if observed["check_rate"] <= args.min_check_rate:
        reasons.append("check_rate_limit")
    return ("fail" if reasons else "pass"), reasons, observed


def run_probe(vus: int, number: int, args: argparse.Namespace, out: Path, deadline: float) -> dict:
    remaining = deadline - time.monotonic()
    if remaining < args.point_seconds + 5:
        return {"vus": vus, "status": "not_run", "reasons": ["five_minute_deadline"]}
    summary_path = out / f"probe_{number:02d}_{vus}vu.json"
    log_path = out / f"probe_{number:02d}_{vus}vu.log"
    env = os.environ.copy()
    env.update({"K6_API_BASE_URL": args.api_url, "K6_VUS": str(vus),
                "K6_DURATION": f"{args.point_seconds}s", "K6_BEARER_TOKEN": "",
                "K6_NOTEBOOK_ID": "", "K6_ACTIVE_ORG_ID": ""})
    command = [args.k6, "run", "--summary-export", str(summary_path), str(SCRIPT)]
    print(f"\nProbe {number}: {vus:,} VUs for {args.point_seconds}s; {remaining:.0f}s remain", flush=True)
    started = time.monotonic()
    timed_out = False
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                   errors="replace", bufsize=1)

        suppressed_warnings = 0

        def stream() -> None:
            nonlocal suppressed_warnings
            assert process.stdout is not None
            for line in process.stdout:
                log.write(line)
                log.flush()
                if 'msg="Request Failed"' in line:
                    suppressed_warnings += 1
                    if suppressed_warnings > 3:
                        continue
                encoding = sys.stdout.encoding or "utf-8"
                sys.stdout.write(line.encode(encoding, errors="replace").decode(encoding))
                sys.stdout.flush()

        reader = threading.Thread(target=stream, daemon=True)
        reader.start()
        try:
            stop_at = min(deadline - 3, started + args.point_seconds + 30)
            while process.poll() is None:
                left = stop_at - time.monotonic()
                if left <= 0:
                    raise subprocess.TimeoutExpired(command, timeout=args.point_seconds + 30)
                try:
                    process.wait(timeout=min(5, left))
                except subprocess.TimeoutExpired:
                    print(f"Probe {number}: {time.monotonic() - started:.0f}s elapsed; "
                          f"{max(0, deadline - time.monotonic()):.0f}s total budget remains", flush=True)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=3)
    if suppressed_warnings > 3:
        print(f"Suppressed {suppressed_warnings - 3:,} repeated request warnings in terminal; "
              f"full output is in {log_path}", flush=True)
    row = {"vus": vus, "duration_target_s": args.point_seconds,
           "elapsed_s": round(time.monotonic() - started, 2), "exit_code": process.returncode,
           "summary_file": str(summary_path), "log_file": str(log_path)}
    if timed_out or not summary_path.is_file():
        row.update(status="invalid", reasons=["generator_timeout" if timed_out else "no_k6_summary"])
        return row
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        status, reasons, observed = evaluate(payload.get("metrics") or {}, vus, args.point_seconds, args)
    except (OSError, ValueError, TypeError) as exc:
        row.update(status="invalid", reasons=[f"unreadable_summary:{type(exc).__name__}"])
        return row
    if process.returncode != 0 and status == "pass":
        status, reasons = "invalid", [f"k6_exit_{process.returncode}_despite_passing_metrics"]
    row.update(status=status, reasons=reasons, **observed)
    print(f"Result: {status.upper()}, p95={observed['p95_ms']} ms, errors={observed['failure_rate']}, "
          f"requests={observed['request_count']}; {', '.join(reasons) or 'all criteria passed'}", flush=True)
    return row


def write_tables(out: Path, result: dict) -> None:
    save(out / "report.json", result)
    fields = ["vus", "status", "reasons", "p95_ms", "failure_rate", "check_rate", "request_count",
              "request_rate", "max_active_vus", "elapsed_s", "exit_code", "summary_file", "log_file"]
    with (out / "probes.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for trial in result["trials"]:
            writer.writerow({**trial, "reasons": ";".join(trial.get("reasons", []))})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--min-vus", type=int, default=8000)
    parser.add_argument("--max-vus", type=int, default=14000)
    parser.add_argument("--budget-seconds", type=int, default=300)
    parser.add_argument("--point-seconds", type=int, default=25)
    parser.add_argument("--max-probes", type=int, default=8)
    parser.add_argument("--p95-ms", type=float, default=1500)
    parser.add_argument("--max-failure-rate", type=float, default=0.05)
    parser.add_argument("--min-check-rate", type=float, default=0.99)
    parser.add_argument("--min-requests-per-vu-second", type=float, default=0.5)
    parser.add_argument("--results", type=Path)
    args = parser.parse_args()
    started_clock = time.monotonic()
    if not 1 <= args.min_vus <= args.max_vus or args.budget_seconds > 300 or args.budget_seconds < 60:
        parser.error("require 1 <= min-vus <= max-vus and a 60–300 second total budget")
    if args.point_seconds < 10 or args.max_probes < 2:
        parser.error("point-seconds must be >=10 and max-probes >=2")
    if not 0 < args.max_failure_rate < 1 or not 0 < args.min_check_rate < 1:
        parser.error("rate thresholds must be between 0 and 1")
    k6 = shutil.which("k6")
    if not k6:
        parser.error("k6 is not on PATH")
    args.k6 = k6
    try:
        preflight(args.api_url)
    except (OSError, ValueError, TimeoutError) as exc:
        parser.error(f"Eduground API preflight failed: {exc}")
    base = args.results or ROOT / "evaluation" / "test_runs"
    base.mkdir(parents=True, exist_ok=True)
    out = base / ("health-peak-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}")
    out.mkdir()
    deadline = started_clock + args.budget_seconds
    result = {"kind": "unauthenticated_health_peak_search", "api_url": args.api_url,
              "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "workload_sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
              "load_generator": {"platform": platform.platform(), "logical_cpus": os.cpu_count(),
                                 "k6_executable": k6},
              "limits": {"min_vus": args.min_vus, "max_vus": args.max_vus,
                         "budget_seconds": args.budget_seconds, "point_seconds": args.point_seconds,
                         "max_probes": args.max_probes, "p95_ms": args.p95_ms,
                         "max_failure_rate": args.max_failure_rate, "min_check_rate": args.min_check_rate,
                         "min_requests_per_vu_second": args.min_requests_per_vu_second},
              "trials": [], "highest_passing_tested_vus": None,
              "scope": "Unauthenticated /health load from one local generator; not distinct students or chat capacity.",
              "assumptions": ["Pass/fail is approximately monotonic over this fixed workload.",
                              "Short, unreplicated probes yield an observed bracket, not a production capacity guarantee."]}
    print(f"Results: {out}", flush=True)

    def probe(vus: int) -> dict:
        row = run_probe(vus, len(result["trials"]) + 1, args, out, deadline)
        result["trials"].append(row)
        passing = [item["vus"] for item in result["trials"] if item["status"] == "pass"]
        result["highest_passing_tested_vus"] = max(passing, default=None)
        write_tables(out, result)
        return row

    low = probe(args.min_vus)
    if low["status"] != "pass":
        result["conclusion"] = "No defensible passing count: lower endpoint failed or the generator was inconclusive."
    elif args.min_vus == args.max_vus:
        result["conclusion"] = "Only one requested VU count was tested; no upper capacity bound was measured."
    else:
        high = probe(args.max_vus)
        if high["status"] == "pass":
            result["conclusion"] = "Upper endpoint passed; capacity above the requested range is unknown."
        elif high["status"] in {"invalid", "not_run"}:
            result["conclusion"] = "Upper endpoint was inconclusive or not run; no failure bracket was established."
        else:
            passing, failing = args.min_vus, args.max_vus
            while len(result["trials"]) < args.max_probes and failing - passing > 1:
                midpoint = (passing + failing) // 2
                row = probe(midpoint)
                if row["status"] == "not_run":
                    break
                if row["status"] == "invalid":
                    result["conclusion"] = "Generator/summary failure during search; bracket is incomplete."
                    break
                if row["status"] == "pass":
                    passing = midpoint
                else:
                    failing = midpoint
            result.setdefault("conclusion", "Highest passing tested count is the current lower bracket, not an exact peak.")
            result["passing_lower_bound_vus"] = passing
            result["failing_upper_bound_vus"] = failing
            result["bracket_width_vus"] = failing - passing
    result["elapsed_total_s"] = round(time.monotonic() - started_clock, 2)
    result["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    write_tables(out, result)
    print(f"\nHighest passing tested VUs: {result['highest_passing_tested_vus']}", flush=True)
    print(result["conclusion"], flush=True)
    print(f"Saved: {out / 'report.json'} and {out / 'probes.csv'}", flush=True)
    return 0 if result["highest_passing_tested_vus"] is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
