"""Read-only evaluation plan; this command never calls a provider."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evaluation.program.config import EvaluationSettings
from evaluation.program.preflight import plan_evaluation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", action="append", required=True, help="v2 course dataset JSON; repeat per course")
    parser.add_argument("--metric", action="append", required=True, help="implemented metric ID; repeat as needed")
    parser.add_argument("--condition", action="append", required=True, help="experimental condition; repeat as needed")
    args = parser.parse_args()
    plan = plan_evaluation(args.dataset, metric_ids=args.metric, conditions=args.condition, settings=EvaluationSettings())
    print(json.dumps(plan, indent=2))
    return 0 if plan["execution_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
