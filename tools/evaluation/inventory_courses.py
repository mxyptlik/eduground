"""Print a read-only, dry-run inventory of course files as JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evaluation.program.corpus_inventory import InventoryError, inventory_courses  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Course root containing one directory per course")
    parser.add_argument("--registry", type=Path, help="Optional .csv or .json course registry; defaults to root/courses.csv")
    args = parser.parse_args(argv)
    try:
        manifest = inventory_courses(args.root, args.registry)
    except (InventoryError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Course inventory failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
