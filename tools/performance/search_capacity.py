"""Bounded, repeatable search of authenticated read capacity.

Requires a running API and distinct test identities. Never starts paid chat.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from threading import Thread
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tests/system/load/capacity.js"


def _command_output(args: list[str]) -> str | None:
    try:
        return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                              check=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def run_manifest(*, k6: str, args, identity_count: int) -> dict:
    git = ["git", "-c", f"safe.directory={ROOT.as_posix()}"]
    git_status = _command_output([*git, "status", "--porcelain"])
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "workload": "authenticated_reads",
        "endpoints": ["GET /api/notebooks", "GET /api/notebooks/{id}/sources",
                      "GET /api/notebooks/{id}/notes"],
        "provider_calls": 0,
        "identity_count": identity_count,
        "base_url": args.base_url,
        "vu_range": [args.min_vus, args.max_vus],
        "probe_seconds": args.probe_seconds,
        "confirm_seconds": args.confirm_seconds,
        "budget_seconds": args.budget_seconds,
        "think_seconds": os.getenv("K6_THINK_SECONDS", "2"),
        "git_commit": _command_output([*git, "rev-parse", "HEAD"]),
        "git_dirty": bool(git_status) if git_status is not None else None,
        "k6_version": _command_output([k6, "version"]),
        "host": {"platform": platform.platform(), "logical_cpus": os.cpu_count()},
        "limitations": ["No model generation", "Source revision and cache condition not auto-verified",
                        "Generator and service resource use must be captured separately"],
    }


def result_from_summary(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    metrics = data.get("metrics") or {}
    durations = metrics.get("http_req_duration{name:read}", metrics.get("http_req_duration", {})).get("values", {})
    failures = metrics.get("http_req_failed", {}).get("values", {})
    checks = metrics.get("checks", {}).get("values", {})
    p95 = durations.get("p(95)")
    error_rate = failures.get("rate")
    check_rate = checks.get("rate")
    return {
        "p95_ms": p95,
        "error_rate": error_rate,
        "check_rate": check_rate,
        "passed": p95 is not None and p95 < 1000 and error_rate is not None and error_rate < .01
                  and check_rate is not None and check_rate > .99,
    }


def _capture(process, log):
    for line in process.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        log.write(line)
        log.flush()


def run_once(k6, output, identity_file, base_url, vus, duration, deadline):
    label = f"{vus}vu-{duration}s"
    summary_path = output / f"{label}.json"
    log_path = output / f"{label}.log"
    env = dict(os.environ)
    env.update(K6_IDENTITIES_FILE=str(identity_file), K6_API_BASE_URL=base_url,
               K6_WORKLOAD="read", PRODUCT_VUS=str(vus), PRODUCT_DURATION=f"{duration}s",
               K6_SUMMARY_FILE=str(summary_path))
    # Prevent a caller's chat opt-in flag from changing this read-only search.
    env.pop("K6_ALLOW_LIVE_MODEL_CALLS", None)
    for key in ("K6_VUS", "K6_DURATION", "K6_ITERATIONS", "K6_STAGES"):
        env.pop(key, None)
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen([k6, "run", str(SCRIPT)], cwd=ROOT, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace")
        reader = Thread(target=_capture, args=(process, log), daemon=True)
        reader.start()
        try:
            process.wait(timeout=max(1, deadline - monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        reader.join(timeout=5)
    result = result_from_summary(summary_path) if summary_path.exists() else {"passed": False}
    result.update(vus=vus, duration_seconds=duration, exit_code=process.returncode,
                  summary=str(summary_path), log=str(log_path))
    result["passed"] = result["passed"] and process.returncode == 0
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identities", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--min-vus", type=int, default=5)
    parser.add_argument("--max-vus", type=int, default=100)
    parser.add_argument("--budget-seconds", type=int, default=300)
    parser.add_argument("--probe-seconds", type=int, default=20)
    parser.add_argument("--confirm-seconds", type=int, default=35)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    k6 = shutil.which("k6")
    if not k6:
        parser.error("k6 executable was not found on PATH")
    if not args.identities.is_file():
        parser.error("identity file does not exist")
    identities = json.loads(args.identities.read_text(encoding="utf-8"))
    if args.min_vus < 1 or args.max_vus < args.min_vus or args.max_vus > len(identities):
        parser.error("VU range must fit distinct identities")
    if args.budget_seconds < args.confirm_seconds + args.probe_seconds:
        parser.error("time budget must fit one probe and one confirmation")
    output = args.output or ROOT / "evaluation/test_runs" / f"product-read-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_text(json.dumps(run_manifest(
        k6=k6, args=args, identity_count=len(identities)), indent=2), encoding="utf-8")
    deadline = monotonic() + args.budget_seconds
    results = []
    passing, failing, candidate = 0, args.max_vus + 1, args.min_vus

    def probe(vus, seconds):
        result = run_once(k6, output, args.identities.resolve(), args.base_url,
                          vus, seconds, deadline)
        results.append(result)
        (output / "report.json").write_text(json.dumps({
            "workload": "authenticated_reads", "provider_calls": 0,
            "identities": len(identities), "time_budget_seconds": args.budget_seconds,
            "results": results, "highest_confirmed_vus": None,
        }, indent=2), encoding="utf-8")
        return result["passed"]

    while candidate < failing and monotonic() + args.probe_seconds + args.confirm_seconds < deadline:
        if probe(candidate, args.probe_seconds):
            passing = candidate
            candidate = min(args.max_vus, candidate * 2)
            if candidate == passing:
                break
        else:
            failing = candidate
            break
    while failing - passing > 1 and monotonic() + args.probe_seconds + args.confirm_seconds < deadline:
        candidate = (passing + failing) // 2
        if probe(candidate, args.probe_seconds):
            passing = candidate
        else:
            failing = candidate
    confirmed = None
    if passing and monotonic() + args.confirm_seconds < deadline:
        if probe(passing, args.confirm_seconds):
            confirmed = passing
    report = json.loads((output / "report.json").read_text(encoding="utf-8")) if results else {"results": []}
    report.update(highest_confirmed_vus=confirmed, interpretation=(
        "Preliminary authenticated-read operating point, not concurrent AI students" if confirmed else
        "No VU count passed both probe and confirmation"))
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"highest_confirmed_vus": confirmed, "output": str(output)}))


if __name__ == "__main__":
    main()
