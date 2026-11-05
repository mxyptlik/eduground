"""Export complete current chunks for one indexed course notebook."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.db.session import SessionLocal  # noqa: E402
from evaluation.program.corpus_inventory import inventory_courses  # noqa: E402
from evaluation.program.evidence_export import export_course_evidence, write_evidence_packet  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--notebook-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--course-root", type=Path, help="Root containing original course files")
    parser.add_argument("--registry", type=Path)
    args = parser.parse_args()
    expected = None
    if args.course_root:
        inventory = inventory_courses(args.course_root, args.registry)
        matches = [course for course in inventory["courses"] if course["course_id"] == args.course_id]
        if len(matches) != 1 or matches[0]["notebook_id"] != args.notebook_id:
            parser.error("course registry must map this course to this notebook")
        expected = {item["sha256"] for item in inventory["documents"]
                    if item["course_id"] == args.course_id and item["status"] == "candidate"}
        if not expected:
            parser.error("no supported nonempty original files in selected course")
    with SessionLocal() as session:
        packet = export_course_evidence(session, course_id=args.course_id,
                                        notebook_id=args.notebook_id, expected_sha256=expected)
    digest = write_evidence_packet(packet, args.output)
    print(json.dumps({"path": str(args.output), "sha256": digest,
                      "source_count": len(packet["sources"]),
                      "chunk_count": sum(len(source["chunks"]) for source in packet["sources"]),
                      "corpus_membership": packet["corpus_membership"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
