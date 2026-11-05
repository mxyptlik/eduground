"""Freeze a human-reviewed dataset against currently indexed Eduground sources."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.db.session import SessionLocal  # noqa: E402
from evaluation.program.db_evidence import DatabaseEvidenceResolver  # noqa: E402
from evaluation.program.reviews import ReviewError, release_packet, validate_bundle, write_release  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True, help="JSON export from the local review workspace")
    parser.add_argument("--course-id", required=True, help="Choose one course in the bundle")
    parser.add_argument("--output", type=Path, required=True, help="New release directory; must not exist")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite {args.output}")
    try:
        bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
        validate_bundle(bundle)
        packets = [p for p in bundle["packets"] if p["course_id"] == args.course_id]
        if len(packets) != 1:
            raise ReviewError("Expected exactly one review packet for the requested course")
        packet = packets[0]
        notebooks = {s["notebook_id"] for s in packet["dataset"]["source_manifest"]}
        if len(notebooks) != 1:
            raise ReviewError("Review packet sources must belong to one notebook")
        with SessionLocal() as db:
            release = release_packet(packet, DatabaseEvidenceResolver(db, notebook_id=next(iter(notebooks))))
        write_release(release, args.output)
    except (OSError, json.JSONDecodeError, ReviewError, ValueError) as exc:
        raise SystemExit(f"Review release failed: {exc}") from exc
    print(json.dumps({"output": str(args.output), "course_id": args.course_id,
                      "accepted": release.counts["Accept"], "counts": release.counts}, indent=2))


if __name__ == "__main__":
    main()
