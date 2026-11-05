"""Bounded, read-only checks used by the M8–M11 Git Bash runner."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def health_request(url: str) -> dict:
    started = time.perf_counter()
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=5) as response:
            response.read(4096)
            return {"status": response.status, "latency_ms": round((time.perf_counter() - started) * 1000, 3)}
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return {"status": getattr(exc, "code", None), "error": type(exc).__name__,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3)}


def percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percent / 100 + 0.999999)))
    return round(ordered[index], 3)


def health(args: argparse.Namespace) -> int:
    url = args.api_url.rstrip("/") + "/health"
    levels = []
    for workers in (1, 5, 10):
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(health_request, url) for _ in range(20)]
            rows = [future.result() for future in as_completed(futures)]
        elapsed = time.perf_counter() - started
        latencies = [row["latency_ms"] for row in rows]
        level = {"concurrency": workers, "requests": len(rows),
                 "successes": sum(row["status"] == 200 for row in rows),
                 "failures": sum(row["status"] != 200 for row in rows),
                 "wall_seconds": round(elapsed, 3),
                 "requests_per_second": round(len(rows) / elapsed, 3),
                 "latency_ms": {"mean": round(statistics.mean(latencies), 3),
                                "p50": percentile(latencies, 50), "p95": percentile(latencies, 95),
                                "p99": percentile(latencies, 99)},
                 "error_types": sorted({row.get("error", f"HTTP {row['status']}") for row in rows if row["status"] != 200})}
        levels.append(level)
        print(json.dumps(level), flush=True)
    result = {"check": "health_concurrency_smoke", "api_url": url, "levels": levels,
              "scope": "Read-only /health endpoint; no authenticated users, chat, ingestion, or provider calls."}
    write_json(args.output, result)
    return 0 if all(level["failures"] == 0 for level in levels) else 1


def parse_one(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT / "apps" / "worker"))
    from app.services.parsing import parse_source_bytes

    path = Path(args.file)
    document = parse_source_bytes(path.read_bytes(), path.name)
    body = document.text.strip()
    print(json.dumps({"file": str(path), "suffix": path.suffix.lower(),
                      "extraction_method": document.metadata.get("extraction_method"),
                      "segments": len(document.segments), "text_chars": len(body),
                      "requires_fallback_ocr": document.metadata.get("requires_fallback_ocr"),
                      "scanned_page_ratio": document.metadata.get("scanned_page_ratio")}), flush=True)
    return 0


def formats(args: argparse.Namespace) -> int:
    from evaluation.program.corpus_inventory import inventory_courses
    from evaluation.program.reports.format_matrix import format_recognition_matrix

    inventory = inventory_courses(args.courses)
    matrix = format_recognition_matrix(inventory)
    print(json.dumps({"inventory_summary": inventory["summary"],
                      "recognition_matrix": matrix}, indent=2), flush=True)
    samples = []
    for suffix in (".pdf", ".pptx", ".docx"):
        candidates = sorted((item for item in inventory["documents"] if item["suffix"] == suffix
                             and 0 < item["byte_size"] <= args.max_bytes),
                            key=lambda item: item["byte_size"])
        if not candidates:
            samples.append({"suffix": suffix, "status": "not_sampled", "reason": "no file within byte limit"})
            continue
        item = candidates[0]
        path = args.courses.resolve() / item["relative_path"]
        try:
            completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "_parse-one", "--file", str(path)],
                                       capture_output=True, text=True, timeout=args.timeout_seconds,
                                       cwd=ROOT, check=False)
            if completed.returncode == 0:
                result = json.loads(completed.stdout)
                result["status"] = "parsed"
            else:
                result = {"file": str(path), "suffix": suffix, "status": "failed",
                          "error": completed.stderr.strip()[-1500:] or completed.stdout.strip()[-1500:]}
        except subprocess.TimeoutExpired:
            result = {"file": str(path), "suffix": suffix, "status": "timed_out"}
        samples.append(result)
        print(json.dumps(result), flush=True)
    report = {"inventory_summary": inventory["summary"], "recognition_matrix": matrix,
              "sample_extractions": samples,
              "scope": "One smallest bounded file per PDF/PPTX/DOCX; no OCR API or extraction-accuracy claim."}
    write_json(args.output, report)
    return 1 if any(row["status"] in {"failed", "timed_out"} for row in samples) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    health_parser = sub.add_parser("health")
    health_parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    health_parser.add_argument("--output", required=True, type=Path)
    formats_parser = sub.add_parser("formats")
    formats_parser.add_argument("--courses", required=True, type=Path)
    formats_parser.add_argument("--output", required=True, type=Path)
    formats_parser.add_argument("--max-bytes", type=int, default=4_000_000)
    formats_parser.add_argument("--timeout-seconds", type=int, default=60)
    single_parser = sub.add_parser("_parse-one")
    single_parser.add_argument("--file", required=True)
    args = parser.parse_args()
    return {"health": health, "formats": formats, "_parse-one": parse_one}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
