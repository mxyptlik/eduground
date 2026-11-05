"""Validate a source-bound question blueprint without making a provider call."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evaluation.program.blueprints import blueprint_plan, parse_blueprint


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_packet", type=Path)
    parser.add_argument("blueprint", type=Path)
    args = parser.parse_args()
    evidence = json.loads(args.evidence_packet.read_text(encoding="utf-8"))
    payload = json.loads(args.blueprint.read_text(encoding="utf-8"))
    plan = blueprint_plan(parse_blueprint(payload, evidence), evidence)
    print(json.dumps(plan, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
