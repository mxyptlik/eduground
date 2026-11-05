"""Inventory course files and optionally upload them through Eduground APIs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evaluation.program.corpus_inventory import InventoryError, inventory_courses  # noqa: E402
from evaluation.program.course_upload import EdugroundApi, upload_courses  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--registry", type=Path, help="CSV or JSON course registry")
    parser.add_argument("--execute", action="store_true", help="Create/reuse notebooks and upload files")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)
    try:
        if args.execute:
            token = os.environ.get("EDUGROUND_EVAL_BEARER_TOKEN", "")
            if not token:
                parser.error("--execute requires EDUGROUND_EVAL_BEARER_TOKEN")
            api = EdugroundApi(args.base_url, token, os.environ.get("EDUGROUND_EVAL_ORGANIZATION_ID"))
            output = upload_courses(args.root, api, args.registry)
        else:
            output = inventory_courses(args.root, args.registry)
    except (InventoryError, OSError, ValueError) as exc:
        print(f"Course upload preparation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 1 if args.execute and output["summary"]["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
