"""Repeat the localhost health-load boundary with abrupt and ramped starts.

This runs only /health against an isolated temporary API and makes no paid calls.
It is Windows-specific because resource samples use the existing Win32 sampler.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.evaluation.measure_laptop_load import sample  # noqa: E402

LOAD_SCRIPT = ROOT / "tests/system/load/health_repeat.js"


def metric(metrics: dict, name: str, field: str, default=None):
    return (metrics.get(name) or {}).get(field, default)


def summarize(trials: list[dict]) -> dict:
    result = {}
    for mode in ("constant", "ramp"):
        rows = [row for row in trials if row["mode"] == mode]
        p95 = [row["p95_ms"] for row in rows if row.get("p95_ms") is not None]
        result[mode] = {
            "trials": len(rows),
            "passes": sum(row["status"] == "pass" for row in rows),
            "failed_trials": sum(row["status"] == "fail" for row in rows),
            "inconclusive_trials": sum(row["status"] == "inconclusive" for row in rows),
            "http_failures": sum(row.get("http_failures", 0) for row in rows),
            "first_request_failures": sum(row.get("first_request_failures", 0) for row in rows),
            "later_request_failures": sum(row.get("later_request_failures", 0) for row in rows),
            "median_p95_ms": statistics.median(p95) if p95 else None,
            "p95_range_ms": [min(p95), max(p95)] if p95 else None,
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--vus", type=int, default=243)
    parser.add_argument("--plateau-seconds", type=int, default=20)
    parser.add_argument("--ramp-seconds", type=int, default=5)
    parser.add_argument("--modes", choices=("both", "constant", "ramp"), default="both")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1 or args.vus < 1 or args.plateau_seconds < 5 or args.ramp_seconds < 1:
        parser.error("repeats and VUs must be positive; plateau >=5 seconds and ramp >=1 second")
    k6 = shutil.which("k6") or r"C:\Program Files\k6\k6.exe"
    if not Path(k6).is_file():
        parser.error("k6 executable not found")
    output = args.output or ROOT / "evaluation/test_runs" / f"health-repeat-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    output.mkdir(parents=True, exist_ok=False)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    report = {
        "scope": "Isolated single-worker /health only; not authenticated students or AI chat",
        "branch": "codex/concurrency-performance",
        "repeats_per_mode": args.repeats,
        "vus": args.vus,
        "plateau_seconds": args.plateau_seconds,
        "ramp_seconds": args.ramp_seconds,
        "modes": args.modes,
        "api_url": base_url,
        "trials": [],
        "summary": {},
    }
    def save():
        report["summary"] = summarize(report["trials"])
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    stop = threading.Event()
    samples: list[dict] = []
    def monitor():
        previous = sample()
        with (output / "resources.jsonl").open("w", encoding="utf-8") as stream:
            while not stop.wait(1):
                row = sample()
                total = row["total"] - previous["total"]
                row["cpu_percent"] = 100 * (1 - (row["idle"] - previous["idle"]) / total) if total else None
                row["monotonic"] = time.monotonic()
                samples.append(row)
                stream.write(json.dumps({key: value for key, value in row.items() if key != "monotonic"}) + "\n")
                stream.flush()
                previous = row

    env = dict(os.environ, PYTHONPATH=str(ROOT / "apps/api"), PYTHONIOENCODING="utf-8",
               CURRICULUM_TUTOR_DATABASE_URL="sqlite:///" + str(output / "isolated.sqlite3").replace("\\", "/"))
    server = None
    thread = None
    print(f"Results: {output}", flush=True)
    with (output / "api.log").open("w", encoding="utf-8") as api_log:
        try:
            server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                       "--port", str(port), "--workers", "1", "--no-access-log"],
                                      cwd=ROOT, env=env, stdout=api_log, stderr=subprocess.STDOUT)
            ready_until = time.monotonic() + 90
            while True:
                if server.poll() is not None:
                    raise RuntimeError("API exited before readiness; inspect api.log")
                try:
                    with urlopen(base_url + "/health", timeout=2) as response:
                        if json.load(response).get("status") == "ok":
                            break
                except OSError:
                    pass
                if time.monotonic() > ready_until:
                    raise RuntimeError("API startup timed out")
                time.sleep(1)
            thread = threading.Thread(target=monitor, daemon=True)
            thread.start()
            for round_number in range(1, args.repeats + 1):
                # Reverse each round to reduce time/order bias.
                modes = (("constant", "ramp") if round_number % 2 else ("ramp", "constant")) if args.modes == "both" else (args.modes,)
                for mode in modes:
                    if server.poll() is not None:
                        raise RuntimeError("API process exited during trials")
                    stem = f"{round_number:02d}_{mode}"
                    summary_path = output / f"{stem}.json"
                    log_path = output / f"{stem}.log"
                    trial_env = dict(os.environ)
                    for key in ("K6_VUS", "K6_DURATION", "K6_ITERATIONS", "K6_STAGES"):
                        trial_env.pop(key, None)
                    trial_env.update(HEALTH_API_BASE_URL=base_url, HEALTH_TARGET_VUS=str(args.vus),
                                     HEALTH_START_MODE=mode, HEALTH_PLATEAU=f"{args.plateau_seconds}s",
                                     HEALTH_RAMP=f"{args.ramp_seconds}s", K6_NO_USAGE_REPORT="true")
                    started = time.monotonic()
                    print(f"Round {round_number}/{args.repeats}, {mode}: {args.vus} VUs", flush=True)
                    with log_path.open("w", encoding="utf-8") as log:
                        try:
                            process = subprocess.run([k6, "run", "--summary-export", str(summary_path), str(LOAD_SCRIPT)],
                                                     cwd=ROOT, env=trial_env, stdout=log, stderr=subprocess.STDOUT,
                                                     timeout=args.plateau_seconds + args.ramp_seconds + 45)
                            exit_code = process.returncode
                        except subprocess.TimeoutExpired:
                            exit_code = None
                    ended = time.monotonic()
                    row = {"round": round_number, "mode": mode, "vus": args.vus,
                           "wall_seconds": round(ended - started, 2), "exit_code": exit_code,
                           "summary_file": summary_path.name, "log_file": log_path.name,
                           "status": "inconclusive"}
                    if summary_path.exists():
                        metrics = json.loads(summary_path.read_text(encoding="utf-8")).get("metrics") or {}
                        row.update(p95_ms=metric(metrics, "http_req_duration", "p(95)"),
                                   failure_rate=metric(metrics, "http_req_failed", "value"),
                                   check_rate=metric(metrics, "checks", "value"),
                                   requests=metric(metrics, "http_reqs", "count", 0),
                                   rps=metric(metrics, "http_reqs", "rate"),
                                   max_vus=metric(metrics, "vus", "max"),
                                   first_request_failures=metric(metrics, "first_request_failures", "count", 0),
                                   later_request_failures=metric(metrics, "later_request_failures", "count", 0))
                        row["http_failures"] = row["first_request_failures"] + row["later_request_failures"]
                        if row["p95_ms"] is not None and row["failure_rate"] is not None:
                            row["status"] = ("pass" if exit_code == 0 and row["failure_rate"] == 0
                                             and row["check_rate"] == 1 and row["p95_ms"] < 1500
                                             and row["max_vus"] == args.vus else "fail")
                    trial_samples = [item for item in samples if started <= item["monotonic"] <= ended]
                    cpu = [item["cpu_percent"] for item in trial_samples if item["cpu_percent"] is not None]
                    row["avg_system_cpu_percent"] = round(statistics.mean(cpu), 2) if cpu else None
                    row["min_available_mib"] = round(min(item["available_bytes"] for item in trial_samples) / 1048576, 1) if trial_samples else None
                    row["api_alive_after"] = server.poll() is None
                    try:
                        with urlopen(base_url + "/health", timeout=3) as response:
                            row["post_trial_health_ok"] = json.load(response).get("status") == "ok"
                    except OSError:
                        row["post_trial_health_ok"] = False
                    report["trials"].append(row)
                    save()
                    print(json.dumps({key: row.get(key) for key in ("round", "mode", "status", "p95_ms", "http_failures",
                                                                        "first_request_failures", "later_request_failures",
                                                                        "avg_system_cpu_percent", "api_alive_after")}), flush=True)
        except Exception as exc:
            report["error"] = str(exc)
            print(f"Experiment error: {exc}", flush=True)
        finally:
            stop.set()
            if thread:
                thread.join(timeout=3)
            if server and server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
            report["api_stopped"] = server is None or server.poll() is not None
            save()
    print(json.dumps({"output": str(output), "summary": report["summary"],
                      "api_stopped": report["api_stopped"], "error": report.get("error")}, indent=2), flush=True)
    return 1 if report.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
