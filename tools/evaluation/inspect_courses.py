"""Read-only notebook adoption and ingestion inspection for course registries."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evaluation.program.course_sources import inspect_course_sources
from evaluation.program.course_upload import EdugroundApi


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Course directory root")
    parser.add_argument("--registry", type=Path, help="CSV/JSON registry with notebook_id mappings")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token-env", default="EDUGROUND_EVAL_BEARER_TOKEN")
    args = parser.parse_args()
    token = os.environ.get(args.token_env, "")
    if not token:
        parser.error(f"{args.token_env} is required")
    api = EdugroundApi(args.api_url, token)
    print(json.dumps(inspect_course_sources(args.root, api, args.registry), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
