"""Render the reconciliation/score/latency/cost report for one collection run."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evaluation.program.artifacts import RunStore
from evaluation.program.contracts import CourseDataset
from evaluation.program.reports.run_report import build_report, render_markdown, reproducibility_bundle


def _git_info() -> dict | None:
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=10)
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=10)
        if revision.returncode != 0:
            return None
        return {
            "revision": revision.stdout.strip(),
            "branch": branch.stdout.strip() or None,
            "dirty_file_count": len([line for line in status.stdout.splitlines() if line.strip()]),
        }
    except (OSError, subprocess.SubprocessError):
        return None


def _replace(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="run directory containing journal.sqlite3")
    parser.add_argument("--release", required=True, help="frozen release directory or dataset JSON")
    parser.add_argument("--condition", action="append", required=True)
    args = parser.parse_args()

    run_path = Path(args.run)
    manifest = json.loads((run_path / "manifest.json").read_text(encoding="utf-8"))
    store = RunStore.open(run_path.parent, run_path.name,
                          configuration_hash=manifest["configuration_hash"],
                          dataset_hash=manifest["dataset_hash"])
    dataset_path = Path(args.release)
    dataset_path = dataset_path / "dataset.json" if dataset_path.is_dir() else dataset_path
    dataset = CourseDataset.model_validate(json.loads(dataset_path.read_text(encoding="utf-8")))
    conditions = tuple(dict.fromkeys(args.condition))

    report = build_report(dataset, store, conditions=conditions)
    out_root = run_path / "artifacts" / "report"
    _replace(out_root / "report.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
    _replace(out_root / "report.md", render_markdown(report))
    bundle = reproducibility_bundle(run_path, git_info=_git_info())
    _replace(out_root / "reproducibility.json", json.dumps(bundle, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "report": str(out_root / "report.json"),
        "markdown": str(out_root / "report.md"),
        "reproducibility": str(out_root / "reproducibility.json"),
        "reconciliation": report["reconciliation"],
        "cost": report["cost"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
