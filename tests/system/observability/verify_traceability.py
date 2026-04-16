from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable
from uuid import uuid4


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify request_id/job_id/run_id traceability contracts.",
    )
    parser.add_argument(
        "--api-base-url",
        default="http://localhost:8000",
        help="Base URL for the API under test.",
    )
    parser.add_argument(
        "--health-path",
        default="/health",
        help="Health endpoint used for request_id verification.",
    )
    parser.add_argument(
        "--request-id",
        default=None,
        help="Optional fixed request id. A UUID-backed id is generated when omitted.",
    )
    parser.add_argument(
        "--job-artifact",
        type=Path,
        default=None,
        help="Optional JSON or JSONL artifact file that must contain job_id records.",
    )
    parser.add_argument(
        "--run-artifact",
        type=Path,
        default=None,
        help="Optional JSON or JSONL artifact file that must contain run_id records.",
    )
    parser.add_argument(
        "--run-id-field",
        default="run_id",
        help="Field name to validate for evaluator-style run correlation.",
    )
    parser.add_argument(
        "--require-trace-id",
        action="store_true",
        help="Require trace_id in artifact records in addition to job_id/run_id.",
    )
    return parser.parse_args()


def load_records(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        raise AssertionError(f"{path} is empty")

    if raw.startswith("{") or raw.startswith("["):
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return [payload]
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        raise AssertionError(f"{path} does not contain a JSON object or array of objects")

    records: list[dict] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise AssertionError(f"{path}:{line_number} is not a JSON object")
        records.append(payload)
    return records


def assert_records_have_fields(
    records: Iterable[dict],
    required_fields: list[str],
    label: str,
) -> None:
    record_count = 0
    for index, record in enumerate(records, start=1):
        record_count += 1
        missing = [field for field in required_fields if not record.get(field)]
        if missing:
            raise AssertionError(
                f"{label} record {index} is missing required fields: {', '.join(missing)}"
            )
    if record_count == 0:
        raise AssertionError(f"{label} artifact did not contain any records")


def verify_request_id(api_base_url: str, health_path: str, request_id: str) -> None:
    url = f"{api_base_url.rstrip('/')}{health_path}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "X-Request-Id": request_id,
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            echoed_request_id = response.headers.get("X-Request-Id")
            if response.status != 200:
                raise AssertionError(f"health endpoint returned {response.status}, expected 200")
            if echoed_request_id != request_id:
                raise AssertionError(
                    "health endpoint did not echo the request id "
                    f"(expected {request_id}, got {echoed_request_id})"
                )
    except urllib.error.URLError as error:
        raise AssertionError(f"failed to call {url}: {error}") from error


def main() -> int:
    args = parse_args()
    request_id = args.request_id or f"system-test-{uuid4().hex}"

    verify_request_id(args.api_base_url, args.health_path, request_id)
    print(json.dumps({"status": "ok", "request_id": request_id, "check": "http_request_id"}))

    if args.job_artifact:
        job_records = load_records(args.job_artifact)
        job_fields = ["job_id"]
        if args.require_trace_id:
            job_fields.append("trace_id")
        assert_records_have_fields(job_records, job_fields, "job")
        print(
            json.dumps(
                {
                    "status": "ok",
                    "artifact": str(args.job_artifact),
                    "check": "job_artifact",
                    "required_fields": job_fields,
                }
            )
        )

    if args.run_artifact:
        run_records = load_records(args.run_artifact)
        run_fields = [args.run_id_field]
        if args.require_trace_id:
            run_fields.append("trace_id")
        assert_records_have_fields(run_records, run_fields, "run")
        print(
            json.dumps(
                {
                    "status": "ok",
                    "artifact": str(args.run_artifact),
                    "check": "run_artifact",
                    "required_fields": run_fields,
                }
            )
        )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
