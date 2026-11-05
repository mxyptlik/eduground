"""Draft deterministic per-course authoring blueprints from evidence packets.

Each course requests 10 questions: 4 easy factual and 4 medium conceptual for
the development split, 2 hard synthesis for the held-out split. Chunk pools
are disjoint across splits, evenly sampled, and every blueprint is validated
and previewed with the offline planner (no provider calls, no spend).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evaluation.program.blueprints import blueprint_plan, parse_blueprint  # noqa: E402

BATCH_SHAPE = (
    (4, "factual", "easy", "development"),
    (4, "conceptual", "medium", "development"),
    (2, "synthesis", "hard", "held_out"),
)
SAMPLE_PER_BATCH = 4


def take_even(pool: list[str], k: int) -> list[str]:
    """Deterministic even sample of up to k items from a chunk pool."""
    if len(pool) <= k:
        return list(pool)
    step = len(pool) / k
    return [pool[int(i * step)] for i in range(k)]


def draft(packet: dict) -> dict:
    ids = [chunk["chunk_id"] for source in packet["sources"] for chunk in source["chunks"]]
    if len(ids) < 6:
        raise ValueError(f"{packet.get('course_id')}: too few chunks ({len(ids)}) for a split-safe blueprint")
    held_n = max(1, min(3, len(ids) // 6))
    held = ids[-held_n:]
    development = ids[:-held_n]
    first = development[0::2]
    second = development[1::2] or first
    pools = (first, second, held)
    batches = []
    for (count, question_type, difficulty, split), pool in zip(BATCH_SHAPE, pools):
        batches.append({
            "count": count,
            "question_type": question_type,
            "difficulty": difficulty,
            "split": split,
            "chunk_ids": take_even(pool, SAMPLE_PER_BATCH),
        })
    return {"course_id": packet["course_id"], "batches": batches}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=ROOT / "evaluation" / "authoring" / "evidence")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "evaluation" / "authoring" / "blueprints")
    parser.add_argument("--plans-dir", type=Path, default=ROOT / "evaluation" / "authoring" / "plans")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.plans_dir.mkdir(parents=True, exist_ok=True)

    packets = sorted(args.evidence_dir.glob("*.json"))
    if not packets:
        print(f"no evidence packets in {args.evidence_dir}")
        return 2
    total = 0
    for packet_path in packets:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        blueprint = draft(packet)
        parsed = parse_blueprint(blueprint, packet)
        plan = blueprint_plan(parsed, packet)
        (args.out_dir / (packet["course_id"] + ".blueprint.json")).write_text(
            json.dumps(blueprint, indent=2) + "\n", encoding="utf-8")
        (args.plans_dir / (packet["course_id"] + ".plan.json")).write_text(
            json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        total += plan["total_requested"]
        print("%-45s requested=%-3d split=%s types=%s" % (
            packet["course_id"], plan["total_requested"],
            plan["targets_by_split"], plan["targets_by_type"]))
    print(f"courses: {len(packets)} | total questions requested: {total} | provider calls: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
