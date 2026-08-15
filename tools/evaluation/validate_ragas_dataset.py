from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from app.evaluation.ragas_dataset import DatasetValidationError, validate_dataset  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an EduGround real-RAGAS dataset before collection or scoring.")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--require-approved", action="store_true")
    args = parser.parse_args()

    try:
        payload = json.loads(args.dataset.read_text(encoding="utf-8"))
        validate_dataset(payload, require_approved=args.require_approved)
    except (OSError, json.JSONDecodeError, DatasetValidationError) as exc:
        raise SystemExit(f"Dataset validation failed: {exc}") from exc

    statuses: dict[str, int] = {}
    for sample in payload["samples"]:
        status = str(sample["review_status"])
        statuses[status] = statuses.get(status, 0) + 1
    print(json.dumps({"dataset": str(args.dataset), "sample_count": len(payload["samples"]), "review_statuses": statuses}, indent=2))


if __name__ == "__main__":
    main()
