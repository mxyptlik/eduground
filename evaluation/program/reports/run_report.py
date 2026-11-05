"""Score, latency, cost and reconciliation report for one collection run.

Reads only immutable run artifacts (journal, scoring tables, budget ledger).
Every cell carries its denominator; N/A, failures and unknown costs stay
visible and are never collapsed into a successful-only average.
"""

from __future__ import annotations

import json
import math
import sqlite3
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence

from evaluation.program.artifacts import RunStore
from evaluation.program.budget import BudgetLedger
from evaluation.program.contracts import CourseDataset
from evaluation.program.runner import summarize_run
from evaluation.program.scoring import completed_answers


def quantiles(values: Sequence[float], qs: Sequence[float], *, minimum: int = 5) -> dict[str, float | None]:
    """Nearest-rank quantiles; p99 requires 100 samples, all others ``minimum``."""
    clean = sorted(float(value) for value in values)
    result: dict[str, float | None] = {}
    for q in qs:
        required = max(minimum, 100 if q >= 0.99 else minimum)
        if len(clean) < required:
            result[f"p{int(q * 100)}"] = None
            continue
        index = min(len(clean) - 1, max(0, math.ceil(q * len(clean)) - 1))
        result[f"p{int(q * 100)}"] = clean[index]
    return result


def _scoring_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else []


def _ledger_state(run_path: Path) -> dict[str, Any]:
    ledger_path = run_path / "budget.sqlite3"
    if not ledger_path.is_file():
        return {"present": False}
    with sqlite3.connect(ledger_path) as db:
        row = db.execute("SELECT cap_usd FROM budget_meta").fetchone()
    if row is None:
        return {"present": True, "error": "budget metadata missing"}
    state = BudgetLedger(ledger_path, row[0]).state()
    return {
        "present": True,
        "cap_usd": str(state.cap_usd),
        "reserved_usd": str(state.reserved_usd),
        "settled_usd": str(state.settled_usd),
        "committed_usd": str(state.committed_usd),
        "unknown_attempts": state.unknown_attempts,
        "overrun_attempts": state.overrun_attempts,
        "by_role_usd": {role: str(value) for role, value in sorted(state.by_role_usd.items())},
    }


def build_report(dataset: CourseDataset, store: RunStore, *, conditions: Sequence[str]) -> dict[str, Any]:
    summary = summarize_run(store, dataset, conditions)
    answers = completed_answers(store, dataset, conditions)
    per_condition: dict[str, dict[str, Any]] = {}
    for condition in conditions:
        rows = [result for (_, row_condition), result in answers.items() if row_condition == condition]
        empty = sum(1 for row in rows if row.get("retrieval_status") == "empty")
        rerank = {}
        for row in rows:
            status = row.get("rerank_status")
            rerank[status] = rerank.get(status, 0) + 1
        per_condition[condition] = {
            "completed": len(rows),
            "empty_retrieval": empty,
            "rerank_status_counts": rerank,
        }

    latencies: dict[str, list[float]] = {"retrieval": [], "rerank": [], "generation": []}
    tokens = {"input_known": 0, "output_known": 0, "unknown_usage_answers": 0, "answers_with_usage": 0}
    for result in answers.values():
        for stage, duration in (result.get("timings_ms") or {}).items():
            if stage in latencies:
                latencies[stage].append(float(duration))
        usage = result.get("answer_usage")
        if isinstance(usage, Mapping):
            tokens["answers_with_usage"] += 1
            input_tokens = usage.get("promptTokenCount", usage.get("prompt_tokens"))
            output_tokens = usage.get("candidatesTokenCount", usage.get("completion_tokens"))
            if isinstance(input_tokens, int) and not isinstance(input_tokens, bool):
                tokens["input_known"] += input_tokens
            if isinstance(output_tokens, int) and not isinstance(output_tokens, bool):
                tokens["output_known"] += output_tokens
        else:
            tokens["unknown_usage_answers"] += 1

    retrieval_rows = _scoring_rows(store.path / "artifacts" / "scoring" / "retrieval.json")
    metric_groups: dict[str, dict[str, Any]] = {}
    for row in retrieval_rows:
        key = f"{row['condition']}:{row['metric_id']}@{row['k']}"
        group = metric_groups.setdefault(key, {"values": [], "na": 0, "relevance_source": row["relevance_source"]})
        if row["value"] is None:
            group["na"] += 1
        else:
            group["values"].append(row["value"])
    metric_summary = {
        key: {
            "n": len(group["values"]),
            "na": group["na"],
            "mean": (sum(group["values"]) / len(group["values"])) if group["values"] else None,
            "relevance_source": group["relevance_source"],
        }
        for key, group in sorted(metric_groups.items())
    }

    citation_rows = _scoring_rows(store.path / "artifacts" / "scoring" / "citations.json")
    citation_counts: dict[str, int] = {}
    for row in citation_rows:
        citation_counts[row["status"]] = citation_counts.get(row["status"], 0) + 1

    ragas_rows = _scoring_rows(store.path / "artifacts" / "scoring" / "ragas.json")
    ragas_summary: dict[str, dict[str, Any]] = {}
    for row in ragas_rows:
        key = f"{row['condition']}:{row['metric_id']}"
        group = ragas_summary.setdefault(key, {"n": 0, "na": 0, "failed": 0, "no_answer": 0, "values": []})
        if row["status"] == "completed" and row["value"] is not None:
            group["n"] += 1
            group["values"].append(row["value"])
        elif row["status"] == "failed":
            group["failed"] += 1
        elif row["status"] == "no_answer":
            group["no_answer"] += 1
        else:
            group["na"] += 1
    for key, group in ragas_summary.items():
        values = group.pop("values")
        group["mean"] = (sum(values) / len(values)) if values else None

    return {
        "report_version": "1.0",
        "course_id": dataset.course_id,
        "dataset_id": dataset.dataset_id,
        "run_id": store.manifest["run_id"],
        "configuration_hash": store.manifest["configuration_hash"],
        "dataset_hash": store.manifest["dataset_hash"],
        "conditions": list(conditions),
        "reconciliation": summary.to_json(),
        "per_condition": per_condition,
        "latency_ms": {stage: quantiles(values, (0.5, 0.95, 0.99)) | {"n": len(values)}
                       for stage, values in latencies.items()},
        "tokens": tokens,
        "cost": _ledger_state(store.path),
        "retrieval_metrics": metric_summary,
        "citation_status_counts": citation_counts,
        "ragas_metrics": ragas_summary,
        "notes": [
            "p95 requires at least 5 samples; p99 at least 100, otherwise null.",
            "unknown usage and unknown prices are counted, never zero-filled.",
            "run-level totals are not a cross-course macro average.",
        ],
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        f"# Evaluation run report: {report['course_id']} ({report['run_id']})",
        "",
        f"- Dataset: `{report['dataset_id']}` · items: {report['reconciliation']['expected_steps']} steps",
        f"- Completed: {report['reconciliation']['completed']} · Failed: "
        f"{report['reconciliation']['failed']} · Missing: {report['reconciliation']['missing']}",
        f"- Configuration hash: `{report['configuration_hash'][:16]}…` · Dataset hash: "
        f"`{report['dataset_hash'][:16]}…`",
        "",
        "## Conditions",
        "",
        "| Condition | Completed | Empty retrieval | Rerank status |",
        "|---|---|---|---|",
    ]
    for condition, row in report["per_condition"].items():
        rerank = ", ".join(f"{k}:{v}" for k, v in sorted(row["rerank_status_counts"].items())) or "—"
        lines.append(f"| {condition} | {row['completed']} | {row['empty_retrieval']} | {rerank} |")
    lines += ["", "## Retrieval metrics (deterministic)", ""]
    if report["retrieval_metrics"]:
        lines += ["| Metric | n | N/A | mean | relevance source |", "|---|---|---|---|---|"]
        for key, row in report["retrieval_metrics"].items():
            mean = "—" if row["mean"] is None else f"{row['mean']:.4f}"
            lines.append(f"| {key} | {row['n']} | {row['na']} | {mean} | {row['relevance_source']} |")
    else:
        lines.append("_No retrieval scoring artifacts in this run._")
    lines += ["", "## RAGAS metrics", ""]
    if report["ragas_metrics"]:
        lines += ["| Metric | n | N/A | failed | no answer | mean |", "|---|---|---|---|---|---|"]
        for key, row in report["ragas_metrics"].items():
            mean = "—" if row["mean"] is None else f"{row['mean']:.4f}"
            lines.append(f"| {key} | {row['n']} | {row['na']} | {row['failed']} | {row['no_answer']} | {mean} |")
    else:
        lines.append("_No RAGAS scoring artifacts in this run._")
    lines += ["", "## Citations", "",
              ", ".join(f"{k}: {v}" for k, v in sorted(report["citation_status_counts"].items()))
              or "_No observed citation markers._",
              "", "## Latency (ms)", "", "| Stage | n | p50 | p95 | p99 |", "|---|---|---|---|---|"]
    for stage, row in report["latency_ms"].items():
        lines.append(f"| {stage} | {row['n']} | {row['p50']} | {row['p95']} | {row['p99']} |")
    lines += ["", "## Cost", ""]
    cost = report["cost"]
    if cost.get("present"):
        lines.append(f"- Committed: {cost['committed_usd']} USD of {cost['cap_usd']} cap; "
                     f"unknown attempts: {cost['unknown_attempts']}")
        for role, value in cost.get("by_role_usd", {}).items():
            lines.append(f"- {role}: {value} USD")
    else:
        lines.append("_No budget ledger in this run._")
    lines.append("")
    return "\n".join(lines)


def _file_digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def reproducibility_bundle(run_path: Path, *, git_info: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Hashes and versions needed to reproduce or audit the run."""
    manifest = json.loads((run_path / "manifest.json").read_text(encoding="utf-8"))
    artifacts = {}
    for path in sorted((run_path / "artifacts").rglob("*")):
        if path.is_file() and path.name != "reproducibility.json":
            artifacts[path.relative_to(run_path).as_posix()] = _file_digest(path)
    import platform
    import sys

    versions: dict[str, str | None] = {"python": sys.version.split()[0], "platform": platform.platform()}
    try:
        from importlib.metadata import version

        versions["ragas"] = version("ragas")
    except Exception:
        versions["ragas"] = None
    return {
        "run_id": manifest["run_id"],
        "configuration_hash": manifest["configuration_hash"],
        "dataset_hash": manifest["dataset_hash"],
        "created_at": manifest.get("created_at"),
        "git": dict(git_info) if git_info is not None else None,
        "versions": versions,
        "artifact_sha256": artifacts,
    }
