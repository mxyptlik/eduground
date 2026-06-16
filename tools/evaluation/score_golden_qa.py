from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Score an arbitrary golden QA JSON file using Eduground's evaluator scorer.")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    sys.path.insert(0, str(Path("apps/evaluator").resolve()))
    from app.runners.golden_qa_runner import run_golden_qa

    scores = run_golden_qa(args.dataset)
    payload = [score.model_dump(mode="json") for score in scores]
    passed = sum(1 for score in scores if score.pass_fail)
    summary = {
        "dataset": str(args.dataset),
        "sample_count": len(scores),
        "passed": passed,
        "failed": len(scores) - passed,
        "pass_rate": round(passed / len(scores), 4) if scores else 0,
        "average_faithfulness_score": round(sum(score.faithfulness_score for score in scores) / len(scores), 4) if scores else 0,
        "average_context_recall_score": round(sum(score.context_recall_score for score in scores) / len(scores), 4) if scores else 0,
    }
    result = {"summary": summary, "scores": payload}
    text = json.dumps(result, indent=2, ensure_ascii=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
