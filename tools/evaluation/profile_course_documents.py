"""Profile every course file independently, with optional guarded OCR, vision, and embeddings.

No API server, Clerk session, Qdrant index, or cross-document embedding batch is used.
Live provider calls are opt-in and share one capped SQLite ledger.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
STAGES = ("read_ms", "parse_ms", "ocr_ms", "visual_ms", "normalize_ms", "chunk_ms",
          "embed_ms", "stage_total_ms", "process_wall_ms")
CALL_ROLES = ("ocr", "ocr_vision", "visual_understanding", "ingestion_embedding")


def ms(start_ns: int) -> float:
    return round((time.perf_counter_ns() - start_ns) / 1_000_000, 3)


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def embedding_batches(texts: list[str], *, max_chunks: int, max_bytes: int) -> list[list[str]]:
    """Bound one file's request size by both item count and UTF-8 text bytes."""
    if max_chunks < 1 or max_bytes < 1:
        raise ValueError("embedding batch limits must be positive")
    batches: list[list[str]] = []
    pending: list[str] = []
    size = 0
    for value in texts:
        byte_count = len(value.encode("utf-8"))
        if byte_count > max_bytes:
            raise ValueError(f"one chunk is {byte_count} bytes, above the {max_bytes}-byte batch limit")
        if pending and (len(pending) >= max_chunks or size + byte_count > max_bytes):
            batches.append(pending)
            pending = []
            size = 0
        pending.append(value)
        size += byte_count
    if pending:
        batches.append(pending)
    return batches


def ledger_rows(ledger_path: Path) -> dict[str, dict]:
    if not ledger_path.is_file():
        return {}
    with sqlite3.connect(ledger_path) as db:
        return {call_id: {"call_id": call_id, "role": role, "reserved_usd": reserved,
                          "actual_usd": actual, "status": status}
                for call_id, role, reserved, actual, status in db.execute(
                    "SELECT call_id,role,reserved_usd,actual_usd,status FROM calls")}


def install_call_trace(guard, calls: list[dict]) -> None:
    original = guard.invoke

    def tracked(**kwargs):
        before = set(ledger_rows(guard.ledger.path))
        started = time.perf_counter_ns()
        error = None
        observed = {}
        extract = kwargs["extract"]

        def capture_usage(raw):
            model, usage, provider_cost = extract(raw)
            observed.update({"response_model": model, "usage": dict(usage),
                             "provider_reported_cost_usd": str(provider_cost) if provider_cost is not None else None})
            return model, usage, provider_cost

        try:
            return original(**{**kwargs, "extract": capture_usage})
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            new = ledger_rows(guard.ledger.path)
            for call_id in new.keys() - before:
                calls.append({**new[call_id], "provider": kwargs["provider"],
                              "model": kwargs["model"], "operation": kwargs["operation"],
                              "bounds": dict(kwargs["bounds"]), "elapsed_ms": ms(started),
                              "error_type": error, **observed})

    guard.invoke = tracked


def call_totals(calls: list[dict], role: str) -> tuple[int, str, int]:
    own = [call for call in calls if call["role"] == role]
    known = sum((Decimal(call["actual_usd"]) for call in own
                 if call["status"] == "settled" and call["actual_usd"] is not None), Decimal("0"))
    return len(own), str(known), sum(call["status"] == "unknown" for call in own)


def child(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT / "apps" / "worker"))
    from app.ledger import load_ledger_guard
    from app.services.chunking import build_chunks
    from app.services.embeddings import embed_texts
    from app.services.figures import scanned_page_ratio
    from app.services.normalization import normalize_document
    from app.services.ocr import run_ocr, should_run_ocr
    from app.services.parsing import parse_source_bytes
    from app.services.visual import enrich_with_figures
    from app.services.vision import build_vision_client
    from app.settings import settings

    started = time.perf_counter_ns()
    path = Path(args._file)
    row = {"path": str(path), "suffix": path.suffix.lower(), "status": "failed",
           **{stage: None for stage in STAGES}, "chunk_count": 0, "embedded_chunks": 0,
           "ocr_status": "not_requested" if not args.ocr else "not_needed",
           "ocr_provider_events": [],
           "visual_status": "not_requested" if not args.vision else "pending",
           "embedding_status": "not_requested" if not args.embed else "pending",
           "embedding_batches": [], "provider_calls": []}
    calls: list[dict] = row["provider_calls"]
    stage = "read"
    t = started
    try:
        guard = load_ledger_guard() if (args.ocr or args.vision or args.embed) else None
        if (args.ocr or args.vision or args.embed) and guard is None:
            raise RuntimeError("live stages require EDUGROUND_EVAL_BUDGET_LEDGER")
        if guard is not None:
            install_call_trace(guard, calls)
        t = time.perf_counter_ns()
        raw = path.read_bytes()
        row.update(read_ms=ms(t), byte_size=len(raw))
        stage = "parse"
        t = time.perf_counter_ns()
        try:
            parsed = parse_source_bytes(raw, path.name)
            parse_exc = None
        except Exception as exc:
            parse_exc = exc
            if path.suffix.lower() != ".pdf" or not args.ocr:
                raise
            parsed = None
        row["parse_ms"] = ms(t)
        if parsed is not None:
            row.update(extraction_method=parsed.metadata.get("extraction_method"),
                       requires_fallback_ocr=bool(parsed.metadata.get("requires_fallback_ocr")),
                       scanned_page_ratio=parsed.metadata.get("scanned_page_ratio"),
                       parsed_text_chars=len(parsed.text))
        scan_ratio = scanned_page_ratio(raw, path.name) if args.vision else None
        ocr_reason = None
        if parse_exc is not None:
            ocr_reason = "pdf_parse_failure"
        elif parsed.metadata.get("requires_fallback_ocr"):
            ocr_reason = "parser_scan_flag"
        elif should_run_ocr(parsed.text, raw, threshold=0.15):
            ocr_reason = "empty_extracted_text"
        elif args.vision and (scan_ratio or 0.0) >= 0.5:
            ocr_reason = "visual_scan_ratio"
        row["ocr_trigger"] = ocr_reason
        row["visual_scan_ratio"] = scan_ratio
        if ocr_reason and args.ocr:
            stage = "ocr"
            t = time.perf_counter_ns()
            row["ocr_status"] = "in_progress"
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            ocr_text = run_ocr(raw, filename=path.name, mime_type=mime, guard=guard,
                               telemetry=row["ocr_provider_events"])
            parsed = parse_source_bytes(ocr_text.encode("utf-8"), f"{path.name}.ocr.txt")
            provider = "deepseek_v4_1_flash" if any(event.get("provider") == "openrouter"
                and event.get("status") == "complete" for event in row["ocr_provider_events"]) else "unstructured"
            row.update(ocr_ms=ms(t), ocr_status="complete", ocr_text_chars=len(ocr_text),
                       ocr_completed_provider=provider)
        elif ocr_reason:
            row["ocr_status"] = "needed_but_disabled"
        if args.vision:
            stage = "visual"
            t = time.perf_counter_ns()
            settings.visual_enabled = True
            vision_env = {"CURRICULUM_TUTOR_OPENROUTER_API_KEY": settings.openrouter_api_key or "",
                          "CURRICULUM_TUTOR_GEMINI_API_KEY": settings.gemini_api_key or ""}
            vision = build_vision_client(guard, environ=vision_env)
            if vision is None:
                raise RuntimeError("configured vision primary provider could not be built")
            if settings.visual_fallback_provider and getattr(vision, "fallback", None) is None:
                raise RuntimeError("configured vision fallback provider could not be built")
            parsed, visual_summary = enrich_with_figures(
                parsed, raw, path.name, settings=settings, guard=guard, vision=vision)
            row.update(visual_ms=ms(t), visual_status=visual_summary.get("status"),
                       visual_summary=visual_summary)
        stage = "normalize"
        t = time.perf_counter_ns()
        normalized = normalize_document(parsed)
        row.update(normalize_ms=ms(t), text_chars=len(normalized.text))
        stage = "chunk"
        t = time.perf_counter_ns()
        chunks = build_chunks(normalized)
        row.update(chunk_ms=ms(t), chunk_count=len(chunks),
                   chunk_tokens=sum(chunk.token_count for chunk in chunks))
        if not chunks:
            raise ValueError("no chunks generated")
        row["status"] = "parsed_and_chunked"
        if args.embed:
            stage = "embed"
            t = time.perf_counter_ns()
            row["embedding_status"] = "in_progress"
            batches = embedding_batches([chunk.text for chunk in chunks],
                                        max_chunks=args.embed_batch_size,
                                        max_bytes=args.embed_max_batch_bytes)
            for index, batch in enumerate(batches, start=1):
                batch_started = time.perf_counter_ns()
                call_start = len(calls)
                vectors = embed_texts(batch, guard=guard)
                if len(vectors) != len(batch) or any(not vector for vector in vectors):
                    raise ValueError("embedding response count or vector was invalid")
                dimensions = {len(vector) for vector in vectors}
                if len(dimensions) != 1 or (row.get("embedding_dimension") and row["embedding_dimension"] not in dimensions):
                    raise ValueError("embedding dimensions changed within the document")
                row["embedded_chunks"] += len(vectors)
                row["embedding_dimension"] = dimensions.pop()
                row["embedding_batches"].append({"index": index, "chunks": len(batch),
                                                 "text_utf8_bytes": sum(len(value.encode("utf-8")) for value in batch),
                                                 "elapsed_ms": ms(batch_started),
                                                 "call_ids": [call["call_id"] for call in calls[call_start:]]})
            row.update(embed_ms=ms(t), embedding_status="complete")
    except Exception as exc:
        row["status"] = "failed"
        row["failed_stage"] = stage
        row["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        if stage in {"ocr", "visual", "embed"}:
            row[f"{'visual' if stage == 'visual' else 'embedding' if stage == 'embed' else 'ocr'}_status"] = "failed"
            row[f"{'visual' if stage == 'visual' else 'embed' if stage == 'embed' else 'ocr'}_ms"] = ms(t)
    finally:
        for role in CALL_ROLES:
            count, cost, unknown = call_totals(calls, role)
            row[f"{role}_requests"] = count
            row[f"{role}_known_cost_usd"] = cost
            row[f"{role}_unknown_attempts"] = unknown
        row["stage_total_ms"] = ms(started)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    return 0 if row["status"] == "parsed_and_chunked" and not row.get("failed_stage") else 1


def stage_stats(rows: list[dict]) -> dict:
    result = {}
    for name in STAGES:
        values = [float(row[name]) for row in rows if isinstance(row.get(name), (int, float))]
        result[name] = {"count": len(values), "total_ms": round(sum(values), 3),
                        "mean_ms": round(statistics.mean(values), 3) if values else None,
                        "median_ms": round(statistics.median(values), 3) if values else None,
                        "p95_ms": round(sorted(values)[math.ceil(0.95 * len(values))-1], 3) if values else None}
    return result


def summarize(rows: list[dict], inventory: dict, started: float, *, ledger: Path | None, modes: dict) -> dict:
    def counts(own):
        return {"documents": len(own),
                "parsed_and_chunked": sum(row["status"] == "parsed_and_chunked" for row in own),
                "failed_or_timed_out": sum(row["status"] != "parsed_and_chunked" for row in own),
                "ocr_needed": sum(bool(row.get("ocr_trigger")) for row in own),
                "ocr_complete": sum(row.get("ocr_status") == "complete" for row in own),
                "chunks": sum(row.get("chunk_count", 0) for row in own),
                "embedded_chunks": sum(row.get("embedded_chunks", 0) for row in own),
                **{f"{role}_requests": sum(row.get(f"{role}_requests", 0) for row in own) for role in CALL_ROLES},
                **{f"{role}_known_cost_usd": str(sum((Decimal(row.get(f"{role}_known_cost_usd", "0"))
                    for row in own), Decimal("0"))) for role in CALL_ROLES},
                "timings": stage_stats(own)}

    courses = {item["course_id"]: counts([row for row in rows if row["course_id"] == item["course_id"]])
               for item in inventory["courses"]}
    result = {"kind": "single_document_course_pipeline_profile", "modes": modes,
              "generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "code_sha256": {name: hashlib.sha256((ROOT / "apps" / "worker" / "app" / "services" / name).read_bytes()).hexdigest()
                              for name in ("parsing.py", "ocr.py", "visual.py", "vision.py", "normalization.py",
                                           "chunking.py", "embeddings.py")},
              "inventory": inventory["summary"], **counts(rows), "courses": courses,
              "elapsed_wall_seconds": round(time.monotonic() - started, 3),
              "scope": "One file per process; parse/OCR/visual/normalize/chunk/optional embed. No indexing. "
                       "OCR/figure output is not a human extraction-accuracy label. "
                       "Recorded costs are known settled costs; unknown attempts may still be billed."}
    if ledger is not None:
        from app.ledger import BudgetLedger
        state = BudgetLedger(ledger, "10").state()
        result["shared_ledger"] = {"path": str(ledger), "cap_usd": str(state.cap_usd),
                                   "committed_usd": str(state.committed_usd), "settled_usd": str(state.settled_usd),
                                   "reserved_usd": str(state.reserved_usd), "unknown_attempts": state.unknown_attempts,
                                   "by_role_usd": {key: str(value) for key, value in state.by_role_usd.items()}}
    return result


def render_csv(path: Path, rows: list[dict]) -> None:
    fields = ["course_id", "relative_path", "source_sha256", "suffix", "status", "failed_stage", "error", "byte_size",
              "extraction_method", "requires_fallback_ocr", "scanned_page_ratio", "parsed_text_chars",
              "ocr_trigger", "ocr_status", "ocr_text_chars", "visual_status", "visual_scan_ratio",
              "ocr_completed_provider",
              "text_chars", "chunk_count", "chunk_tokens", *STAGES, "embedding_status",
              "embedded_chunks", "embedding_dimension", "embedding_batch_count"]
    for role in CALL_ROLES:
        fields.extend((f"{role}_requests", f"{role}_known_cost_usd", f"{role}_unknown_attempts"))
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def render_courses_csv(path: Path, courses: dict) -> None:
    fields = ["course_id", "documents", "parsed_and_chunked", "failed_or_timed_out", "ocr_needed", "ocr_complete",
              "chunks", "embedded_chunks"]
    for role in CALL_ROLES:
        fields.extend((f"{role}_requests", f"{role}_known_cost_usd"))
    for stage in STAGES:
        fields.extend(f"{stage}_{name}" for name in ("count", "total_ms", "mean_ms", "median_ms", "p95_ms"))
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for course_id, course in courses.items():
            row = {"course_id": course_id, **{key: course[key] for key in fields[1:] if key in course}}
            for stage in STAGES:
                row.update({f"{stage}_{key}": value for key, value in course["timings"][stage].items()})
            writer.writerow(row)


def preflight(args, parser) -> dict[str, str]:
    live = args.embed or args.ocr or args.vision
    if not live:
        return {}
    if args.ledger is None:
        parser.error("live stages require --ledger pointing to the shared $10 pilot ledger")
    if not args.prices.is_file():
        parser.error(f"dated price registry missing: {args.prices}")
    sys.path.insert(0, str(ROOT / "apps" / "worker"))
    from app.ledger import load_ledger_guard
    from app.settings import settings

    env = {"EDUGROUND_EVAL_BUDGET_LEDGER": str(args.ledger.resolve()),
           "EDUGROUND_EVAL_MAX_API_SPEND_USD": "10",
           "EDUGROUND_EVAL_PRICE_REGISTRY": str(args.prices.resolve())}
    try:
        guard = load_ledger_guard(env)
        assert guard is not None
        if args.embed:
            if not settings.openrouter_api_key:
                raise ValueError("OpenRouter embedding API key missing")
            guard.schedule(provider="openrouter", model=settings.openrouter_embedding_model,
                           operation="ingestion_embedding")
        if args.ocr:
            if settings.unstructured_api_key:
                guard.schedule(provider="unstructured", model="hosted-api", operation="ocr")
            if settings.ocr_vision_enabled:
                if settings.ocr_vision_model != "deepseek/deepseek-v4.1-flash":
                    raise ValueError("OCR vision fallback must use deepseek/deepseek-v4.1-flash")
                if not settings.openrouter_api_key:
                    raise ValueError("OpenRouter key missing for DeepSeek OCR fallback")
                guard.schedule(provider="openrouter", model=settings.ocr_vision_model,
                               operation="ocr_vision")
            elif not settings.unstructured_api_key:
                raise ValueError("Unstructured key missing and OCR vision fallback is disabled")
        if args.vision:
            if not settings.visual_provider or not settings.visual_model:
                raise ValueError("visual provider/model must be pinned in .env")
            if bool(settings.visual_fallback_provider) != bool(settings.visual_fallback_model):
                raise ValueError("visual fallback needs both provider and model")
            for provider, model in ((settings.visual_provider, settings.visual_model),
                                    (settings.visual_fallback_provider, settings.visual_fallback_model)):
                if not provider:
                    continue
                if provider not in {"openrouter", "gemini"}:
                    raise ValueError(f"unsupported vision provider: {provider}")
                key = settings.openrouter_api_key if provider == "openrouter" else settings.gemini_api_key
                if not key:
                    raise ValueError(f"{provider} vision API key missing")
                guard.schedule(provider=provider, model=model, operation="visual_understanding")
    except Exception as exc:
        parser.error(f"live-stage ledger/configuration preflight failed: {exc}")
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--courses", type=Path, default=ROOT / "Courses")
    parser.add_argument("--expected-courses", type=int, default=17)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=300, help="per document; timed out files are recorded")
    parser.add_argument("--ocr", action="store_true", help="live Unstructured OCR only when the worker would trigger it")
    parser.add_argument("--vision", action="store_true", help="live visual enrichment with configured primary/fallback")
    parser.add_argument("--embed", action="store_true", help="live batched OpenRouter embeddings")
    parser.add_argument("--embed-batch-size", type=int, default=32)
    parser.add_argument("--embed-max-batch-bytes", type=int, default=100_000)
    parser.add_argument("--ledger", type=Path, help="existing/shared pilot ledger for the $10 cap")
    parser.add_argument("--prices", type=Path, default=ROOT / "evaluation" / "prices" / "2026-09-30.openrouter.json")
    parser.add_argument("--_file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._file:
        return child(args)
    if args.timeout_seconds < 10:
        parser.error("timeout-seconds must be >=10")
    if args.embed_batch_size < 1 or args.embed_max_batch_bytes < 1:
        parser.error("embedding batch limits must be positive")
    env = preflight(args, parser)
    from evaluation.program.corpus_inventory import inventory_courses

    started = time.monotonic()
    try:
        inventory = inventory_courses(args.courses)
    except Exception as exc:
        parser.error(f"course inventory failed: {exc}")
    if inventory["summary"]["course_count"] != args.expected_courses:
        parser.error(f"expected {args.expected_courses} courses, found {inventory['summary']['course_count']}")
    base = args.results or ROOT / "evaluation" / "test_runs"
    base.mkdir(parents=True, exist_ok=True)
    out = base / ("course-profile-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}")
    out.mkdir()
    write_json(out / "inventory.json", inventory)
    if args.embed or args.ocr or args.vision:
        shutil.copyfile(args.prices, out / "price_registry.json")
    write_json(out / "run_config.json", {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
                                         "courses": str(args.courses.resolve()),
                                         "expected_courses": args.expected_courses,
                                         "timeout_seconds_per_document": args.timeout_seconds,
                                         "ocr": args.ocr, "vision": args.vision, "embed": args.embed,
                                         "embed_batch_size": args.embed_batch_size,
                                         "embed_max_batch_bytes": args.embed_max_batch_bytes,
                                         "ledger": str(args.ledger.resolve()) if args.ledger else None,
                                         "price_registry": str(args.prices.resolve()) if args.embed or args.ocr or args.vision else None})
    print(f"Attempting {inventory['summary']['file_count']} files from {inventory['summary']['course_count']} courses", flush=True)
    print(f"Results: {out}", flush=True)
    rows = []
    live_allowed = args.embed or args.ocr or args.vision
    with (out / "documents.jsonl").open("w", encoding="utf-8") as sink, \
            (out / "calls.jsonl").open("w", encoding="utf-8") as call_sink:
        for number, item in enumerate(inventory["documents"], start=1):
            path = args.courses.resolve() / item["relative_path"]
            row = {"course_id": item["course_id"], "relative_path": item["relative_path"],
                   "source_sha256": item["sha256"], "suffix": item["suffix"], "status": "unsupported"}
            if not item["supported"]:
                row["error"] = "unsupported file suffix"
            else:
                command = [sys.executable, str(Path(__file__).resolve()), "--_file", str(path)]
                if live_allowed:
                    for flag in ("ocr", "vision", "embed"):
                        if getattr(args, flag):
                            command.append(f"--{flag}")
                    command.extend(("--embed-batch-size", str(args.embed_batch_size),
                                    "--embed-max-batch-bytes", str(args.embed_max_batch_bytes)))
                start_doc = time.perf_counter_ns()
                completed = None
                try:
                    completed = subprocess.run(command, cwd=ROOT, env={**os.environ, **env},
                                               capture_output=True, text=True, timeout=args.timeout_seconds,
                                               check=False)
                    parsed = json.loads(completed.stdout.strip().splitlines()[-1])
                    row.update(parsed)
                    if completed.returncode and row["status"] == "parsed_and_chunked" and not row.get("failed_stage"):
                        row["status"] = "failed"
                        row["error"] = "child exited nonzero after reporting success"
                except subprocess.TimeoutExpired:
                    row.update(status="timed_out", error=f"exceeded {args.timeout_seconds}s per-document limit")
                except (ValueError, OSError, IndexError) as exc:
                    row.update(status="failed", error=f"child result unavailable: {type(exc).__name__}",
                               child_stderr_tail=completed.stderr[-500:] if completed is not None else None)
                row["process_wall_ms"] = ms(start_doc)
            if live_allowed and (row.get("failed_stage") in {"ocr", "visual", "embed"} or row["status"] == "timed_out"
                                 or str(row.get("error", "")).startswith("child result unavailable")):
                live_allowed = False
                print("Live provider calls stopped after failure; remaining files use offline stages.", flush=True)
            elif (args.embed or args.ocr or args.vision) and not live_allowed:
                row["live_status"] = "skipped_after_prior_failure"
            row["embedding_batch_count"] = len(row.get("embedding_batches", []))
            for call in row.get("provider_calls", []):
                call_sink.write(json.dumps({"course_id": item["course_id"],
                                            "relative_path": item["relative_path"], **call}, ensure_ascii=False) + "\n")
            call_sink.flush()
            rows.append(row)
            sink.write(json.dumps(row, ensure_ascii=False) + "\n")
            sink.flush()
            print(f"[{number}/{inventory['summary']['file_count']}] {item['course_id']} | {item['relative_path']} | "
                  f"{row['status']} | parse={row.get('parse_ms')}ms ocr={row.get('ocr_ms')}ms "
                  f"vision={row.get('visual_ms')}ms chunk={row.get('chunk_ms')}ms "
                  f"embed={row.get('embed_ms')}ms calls={len(row.get('provider_calls', []))} "
                  f"wall={row.get('process_wall_ms')}ms", flush=True)
    sys.path.insert(0, str(ROOT / "apps" / "worker"))
    summary = summarize(rows, inventory, started, ledger=args.ledger.resolve() if (args.embed or args.ocr or args.vision) else None,
                        modes={"ocr": args.ocr, "vision": args.vision, "embed": args.embed,
                               "embed_batch_size": args.embed_batch_size,
                               "embed_max_batch_bytes": args.embed_max_batch_bytes})
    if args.embed or args.ocr or args.vision:
        from app.settings import settings
        summary["price_registry_sha256"] = hashlib.sha256(args.prices.read_bytes()).hexdigest()
        summary["provider_config"] = {"embedding": {"provider": "openrouter", "model": settings.openrouter_embedding_model}
                                      if args.embed else None,
                                      "ocr": {"provider": "unstructured", "model": "hosted-api",
                                              "strategy": settings.unstructured_ocr_strategy,
                                              "fallback_enabled": settings.ocr_vision_enabled,
                                              "fallback_provider": "openrouter" if settings.ocr_vision_enabled else None,
                                              "fallback_model": settings.ocr_vision_model if settings.ocr_vision_enabled else None}
                                      if args.ocr else None,
                                      "visual_primary": {"provider": settings.visual_provider,
                                                         "model": settings.visual_model} if args.vision else None,
                                      "visual_fallback": {"provider": settings.visual_fallback_provider,
                                                          "model": settings.visual_fallback_model}
                                      if args.vision and settings.visual_fallback_provider else None}
    write_json(out / "summary.json", summary)
    render_csv(out / "documents.csv", rows)
    render_courses_csv(out / "courses.csv", summary["courses"])
    print(json.dumps({"processed_documents": summary["documents"], "parsed_and_chunked": summary["parsed_and_chunked"],
                      "failed_or_timed_out": summary["failed_or_timed_out"],
                      "elapsed_wall_seconds": summary["elapsed_wall_seconds"],
                      "timings": summary["timings"],
                      "requests": {role: summary[f"{role}_requests"] for role in CALL_ROLES},
                      "known_cost_usd": {role: summary[f"{role}_known_cost_usd"] for role in CALL_ROLES}},
                     indent=2), flush=True)
    print(f"Saved: {out / 'documents.csv'}, {out / 'courses.csv'}, {out / 'documents.jsonl'}, "
          f"{out / 'calls.jsonl'}, {out / 'summary.json'}", flush=True)
    return 1 if summary["failed_or_timed_out"] or any(row.get("failed_stage") for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
