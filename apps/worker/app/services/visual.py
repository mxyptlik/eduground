"""Layer 4 orchestration: figures -> text cards -> normalized segments.

The enrichment is additive and failure-isolated: when anything goes wrong the
original parsed document is returned unchanged, and ingestion proceeds on text
alone. Cards are ordinary segments, so the existing normalize -> chunk -> embed
-> index path (and citation locators) works untouched. Vision-eligible figures
are described in one pass (optionally batched); cached images cost nothing.
"""

from __future__ import annotations

from typing import Any

from app.ledger import LedgerGuard
from app.services.figures import extract_figures, insert_figure_segments, render_card
from app.services.types import NormalizedDocument
from app.services.vision import VisionClient, vision_unavailable_reason


def enrich_with_figures(
    document: NormalizedDocument, raw_bytes: bytes, filename: str, *,
    settings: Any, guard: LedgerGuard | None, vision: VisionClient | None,
) -> tuple[NormalizedDocument, dict[str, Any]]:
    """Append figure cards as segments; never raises for figure problems."""
    if not bool(getattr(settings, "visual_enabled", False)):
        return document, {"status": "disabled"}
    try:
        inventory = extract_figures(
            raw_bytes, filename,
            max_figures=int(getattr(settings, "visual_max_figures_per_document", 40)),
            min_area_fraction=float(getattr(settings, "visual_min_area_fraction", 0.02)),
        )
    except Exception as exc:  # defensive: detection must not break ingestion
        return document, {"status": "extraction_failed", "error": type(exc).__name__}
    summary: dict[str, Any] = {
        "status": inventory.status,
        "figures": len(inventory.records),
        "scanned_pages": inventory.scanned_pages,
        "described": 0,
        "not_described": 0,
        "deduped": 0,
        "oversize": 0,
        "unsupported_format": 0,
    }
    if not inventory.records:
        return document, summary
    reason = vision_unavailable_reason(guard)
    max_image_bytes = int(getattr(settings, "visual_max_image_bytes", 8_388_608))

    planned: list[tuple[Any, str]] = []
    vision_items: list[dict[str, Any]] = []
    vision_ids: list[str] = []
    seen: set[str] = set()
    for record in inventory.records:
        if record.image_sha256:
            if record.image_sha256 in seen:
                summary["deduped"] += 1
                continue
            seen.add(record.image_sha256)
        if record.chart_markdown:
            status = "chart"
        elif record.image_bytes and len(record.image_bytes) > max_image_bytes:
            status = "oversize"
        elif record.image_bytes is None and record.kind == "picture":
            status = "unsupported"
        elif vision is not None and record.image_bytes:
            status = "vision"
            vision_items.append({"image": record.image_bytes, "mime": record.image_mime,
                                 "context": record.caption or record.nearby_text})
            vision_ids.append(record.figure_id)
        else:
            status = "no_vision"
        planned.append((record, status))

    outcomes: dict[str, Any] = {}
    vision_errors: list[str] = []
    if vision_items:
        describe_many = getattr(vision, "describe_many", None)
        if callable(describe_many):
            results = describe_many(vision_items)
        else:  # minimal client used by tests or callers
            results = []
            for item in vision_items:
                try:
                    results.append(vision.describe(item["image"], mime=item["mime"], context=item["context"]))
                except Exception as exc:  # noqa: BLE001 - reported per figure
                    results.append(exc)
        for figure_id, outcome in zip(vision_ids, results):
            if isinstance(outcome, Exception):
                vision_errors.append(type(outcome).__name__)
            else:
                outcomes[figure_id] = outcome

    cards: dict[str, str] = {}
    for record, status in planned:
        understanding = outcomes.get(record.figure_id)
        described = status == "chart" or understanding is not None
        if status == "chart":
            summary["chart_data"] = summary.get("chart_data", 0) + 1
        elif status == "oversize":
            summary["oversize"] += 1
        elif status == "unsupported":
            summary["unsupported_format"] += 1
        if described:
            summary["described"] += 1
        else:
            summary["not_described"] += 1
        cards[record.figure_id] = render_card(record, understanding, filename=filename, described=described)

    inserted = insert_figure_segments(document, [record for record, _ in planned], cards)
    summary["inserted_segments"] = inserted
    if vision_errors:
        summary["vision_errors"] = vision_errors
    if summary["described"]:
        summary["vision"] = "used"
    elif vision is None:
        summary["vision"] = f"unavailable: {reason}" if reason else "unavailable"
    else:
        summary["vision"] = "attempted"
    return document, summary
