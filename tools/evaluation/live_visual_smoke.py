"""Bounded live smoke for the visual-evidence path (image in, card text out).

Detection runs offline on a real course file; only the selected figures are
sent to the pinned vision model, through the shared budget ledger with a dated
price registry. Everything lands in a fresh create-only artifact directory.
Nothing is written to the database, Qdrant or the product storage.

Plan mode (``--dry-run``) detects figures and prints the inventory without a
key, ledger or spend.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import sys
from pathlib import Path
from urllib import request

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "worker"))

try:
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:  # pragma: no cover
    pass

from app.ledger import BudgetLedger, LedgerGuard, PriceRegistry, VISUAL_UNDERSTANDING_OPERATION
from app.services.figures import extract_figures, render_card
from app.services.vision import VisionClient, VisionError, _gemini_identity

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


def _detect(path: Path, limit: int):
    inventory = extract_figures(path.read_bytes(), path.name, max_figures=max(limit * 4, 20))
    return inventory


def _gemini_answer(guard: LedgerGuard, model: str, key: str, prompt: str) -> str:
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 512},
    }
    req = request.Request(
        f"{GEMINI_BASE}/models/{model}:generateContent",
        data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
    )

    def dispatch():
        with request.urlopen(req, timeout=60.0) as response:
            return json.loads(response.read().decode("utf-8"))

    raw = guard.invoke(
        role="answer", provider="gemini", model=model, operation="answer",
        bounds={"max_input_tokens": len(prompt.encode("utf-8")) + 4096, "max_output_tokens": 512},
        dispatch=dispatch, extract=lambda body: _gemini_identity(body, pinned=model),
    )
    parts = raw["candidates"][0]["content"]["parts"]
    return "".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--figures", type=int, default=2)
    parser.add_argument("--provider", default="gemini")
    parser.add_argument("--model", default="gemini-3.5-flash")
    parser.add_argument("--prices", type=Path)
    parser.add_argument("--cap", type=float, default=10.0)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--ask", help="optional question answered from the collected cards")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    inventory = _detect(args.file, args.figures)
    summary = {
        "file": str(args.file), "pages": inventory.page_count, "status": inventory.status,
        "figures": [
            {"figure_id": r.figure_id, "kind": r.kind, "page": r.page,
             "mime": r.image_mime, "original_mime": r.image_original_mime,
             "bytes": len(r.image_bytes) if r.image_bytes else 0,
             "chart_data": bool(r.chart_markdown), "caption": r.caption}
            for r in inventory.records
        ],
        "scanned_pages": inventory.scanned_pages,
    }
    if args.dry_run:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    if not args.prices or not args.out:
        parser.error("--prices and --out are required for a live run")
    cap = Decimal(str(args.cap))
    env_cap = os.environ.get("EDUGROUND_EVAL_MAX_API_SPEND_USD", "").strip()
    if env_cap and Decimal(env_cap) != cap:
        parser.error(f"--cap {cap} does not match EDUGROUND_EVAL_MAX_API_SPEND_USD")
    key = os.environ.get("CURRICULUM_TUTOR_GEMINI_API_KEY", "").strip()
    if not key:
        parser.error("CURRICULUM_TUTOR_GEMINI_API_KEY is required (never printed)")
    if args.out.exists():
        parser.error(f"output directory already exists: {args.out}")
    args.out.mkdir(parents=True)

    ledger = BudgetLedger(args.out / "budget.sqlite3", cap)
    guard = LedgerGuard(ledger, PriceRegistry.from_json(args.prices))
    client = VisionClient(provider=args.provider, model=args.model, api_key=key, guard=guard,
                          cache_dir=args.out / "cache", base_url=GEMINI_BASE)

    figures = [record for record in inventory.records if record.image_bytes][: args.figures]
    if not figures:
        print(json.dumps({"error": "no image-bearing figures detected", **summary}, indent=2))
        return 2
    cards = {}
    results = []
    errors = []
    for record in figures:
        try:
            understanding = client.describe(record.image_bytes, mime=record.image_mime,
                                            context=record.caption or record.nearby_text)
            described = True
        except Exception as exc:  # noqa: BLE001 - report the exact failure
            understanding = None
            described = False
            errors.append({"figure_id": record.figure_id, "error": f"{type(exc).__name__}: {exc}"})
        card = render_card(record, understanding, filename=args.file.name, described=described)
        cards[record.figure_id] = card
        if described:
            results.append({"figure_id": record.figure_id, "kind": record.kind, "page": record.page,
                            "mime": record.image_mime, "card": card,
                            "verbatim_text": understanding.verbatim_text,
                            "description": understanding.description,
                            "uncertainty": list(understanding.uncertainty)})
    answer = None
    answer_error = None
    if args.ask and cards:
        context = "\n\n".join(cards.values())
        try:
            answer = _gemini_answer(guard, args.model, key,
                                    f"Course material figure cards:\n{context}\n\nQuestion: {args.ask}")
        except Exception as exc:  # noqa: BLE001 - a blocked ledger must be reported, not hidden
            answer_error = f"{type(exc).__name__}: {exc}"
    state = ledger.state()
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": args.provider, "model": args.model,
        "file": str(args.file), "figures_detected": summary["figures"],
        "described": len(results), "errors": errors, "results": results, "answer": answer,
        "answer_error": answer_error,
        "ledger": {"cap_usd": str(state.cap_usd), "settled_usd": str(state.settled_usd),
                   "reserved_usd": str(state.reserved_usd), "unknown_attempts": state.unknown_attempts,
                   "by_role_usd": {role: str(value) for role, value in state.by_role_usd.items()}},
    }
    (args.out / "smoke_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                                encoding="utf-8")
    print(json.dumps({"out": str(args.out), "described": len(results), "errors": errors,
                      "answer_error": answer_error,
                      "ledger": report["ledger"], "answer": answer}, indent=2, ensure_ascii=False))
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
