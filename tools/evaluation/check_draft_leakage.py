"""Report duplicate questions and development/held-out evidence overlap."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evaluation.program.contracts import CourseDataset
from evaluation.program.draft_leakage import draft_leakage_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("evidence_packet", type=Path)
    args = parser.parse_args()
    dataset = CourseDataset.model_validate_json(args.dataset.read_text(encoding="utf-8"))
    evidence = json.loads(args.evidence_packet.read_text(encoding="utf-8"))
    print(json.dumps(draft_leakage_report(dataset, evidence), indent=2))


if __name__ == "__main__":
    main()
