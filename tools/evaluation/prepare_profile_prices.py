"""Merge dated evaluation price registries and add a sourced OCR rate if requested.

This writes a new registry; it never calls a provider or guesses a price.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def merge_registries(paths: list[Path], *, ocr_price: dict | None = None) -> dict:
    schedules = []
    identities = set()
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != "1.0" or not isinstance(payload.get("schedules"), list):
            raise ValueError(f"invalid price registry: {path}")
        for schedule in payload["schedules"]:
            identity = (schedule["provider"], schedule["model"], schedule["operation"],
                        schedule["effective_from"])
            if identity not in identities:
                schedules.append(schedule)
                identities.add(identity)
    if ocr_price is not None:
        identity = ("unstructured", "hosted-api", "ocr", ocr_price["effective_from"])
        if identity in identities:
            raise ValueError("OCR price schedule already exists for this date")
        schedules.append(ocr_price)
    return {"version": "1.0", "schedules": schedules}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", default=[], help="dated registry; repeatable")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ocr-unit", choices=("pages", "requests"))
    parser.add_argument("--ocr-amount-usd", type=Decimal)
    parser.add_argument("--ocr-quantity", type=int, default=1)
    parser.add_argument("--ocr-source", help="URL or dated contract/invoice reference for the OCR rate")
    parser.add_argument("--effective-from", default=datetime.now(timezone.utc).isoformat())
    args = parser.parse_args()
    inputs = args.input or [ROOT / "evaluation" / "prices" / "2026-09-30.openrouter.json",
                             ROOT / "evaluation" / "prices" / "2026-09-30.gemini.json",
                             ROOT / "evaluation" / "prices" / "2026-09-30.deepseek-ocr.json"]
    ocr_fields = (args.ocr_unit, args.ocr_amount_usd, args.ocr_source)
    if any(value is not None for value in ocr_fields) and not all(value is not None for value in ocr_fields):
        parser.error("OCR rate requires --ocr-unit, --ocr-amount-usd, and --ocr-source together")
    if args.ocr_quantity < 1 or (args.ocr_amount_usd is not None and
                                  (not args.ocr_amount_usd.is_finite() or args.ocr_amount_usd < 0)):
        parser.error("OCR quantity must be positive and amount finite/non-negative")
    ocr_price = None
    if args.ocr_unit:
        ocr_price = {"provider": "unstructured", "model": "hosted-api", "operation": "ocr",
                     "effective_from": args.effective_from, "currency": "USD", "source": args.ocr_source,
                     "prices": [{"unit": args.ocr_unit, "amount": str(args.ocr_amount_usd),
                                 "quantity": args.ocr_quantity}]}
    registry = merge_registries(inputs, ocr_price=ocr_price)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(registry['schedules'])} schedules to {args.output}")
    print("OCR priced: " + str(ocr_price is not None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
