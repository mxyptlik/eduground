"""Cross-course duplicate and leakage disposition report; read-only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evaluation.program.contracts import CourseDataset, adapt_legacy_v1
from evaluation.program.corpus_inventory import inventory_courses
from evaluation.program.leakage import cross_course_leakage_report


def _load_dataset(path: Path) -> CourseDataset:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and payload.get("schema_version") == 2:
        return CourseDataset.model_validate(payload)
    return adapt_legacy_v1(payload, course_id=path.stem)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--courses-root", type=Path, help="scan a course directory (read-only)")
    source.add_argument("--inventory", type=Path, help="reuse a persisted inventory snapshot")
    parser.add_argument("--registry", type=Path, help="optional courses.csv mapping for --courses-root")
    parser.add_argument("--dataset", type=Path, action="append", default=[],
                        help="draft dataset JSON; repeat per course")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--force", action="store_true", help="replace an existing report")
    args = parser.parse_args()

    if args.out.exists() and not args.force:
        parser.error(f"report exists: {args.out}; pass --force to replace it")
    if args.inventory:
        inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    else:
        inventory = inventory_courses(args.courses_root, args.registry)
    datasets = [_load_dataset(path) for path in args.dataset]
    report = cross_course_leakage_report(inventory, datasets)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.out), **report["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
