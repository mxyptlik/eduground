from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from statistics import mean, median


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{2,}", text.lower()))


def coverage(target: set[str], evidence: set[str]) -> float:
    if not target:
        return 0.0
    return len(target & evidence) / len(target)


def percentile(values: list[int], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))
    return float(ordered[index])


def load_score_map(path: Path) -> tuple[dict, dict[str, dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["summary"], {item["sample_key"]: item for item in payload["scores"]}


def summarize(results_path: Path, scores_path: Path) -> dict:
    results = json.loads(results_path.read_text(encoding="utf-8"))
    score_summary, score_map = load_score_map(scores_path)
    retrieval_latencies = []
    end_to_end_latencies = []
    model_latencies = []
    answer_relevance_scores = []
    citation_support_scores = []
    source_hits = 0
    citation_presence = 0
    failed_rows = []

    for sample in results:
        expected_source = str(sample.get("source_label") or "")
        citations = sample.get("eduground_citations") or []
        citation_text = "\n\n".join(str(item.get("quote_text") or "") for item in citations)
        answer_text = str(sample.get("model_answer") or "")
        expected_text = str(sample.get("expected_answer") or "")
        question_text = str(sample.get("question") or "")
        trace = sample.get("eduground_retrieval_trace") or {}
        retrieval_latencies.append(int(trace.get("latency_ms") or 0))
        if sample.get("eduground_end_to_end_latency_ms") is not None:
            end_to_end_latencies.append(int(sample["eduground_end_to_end_latency_ms"]))
        if sample.get("eduground_model_latency_ms") is not None:
            model_latencies.append(int(sample["eduground_model_latency_ms"]))
        if citations:
            citation_presence += 1
        citation_labels = [str(item.get("display_label") or item.get("source_title") or "") for item in citations]
        if any(expected_source in label for label in citation_labels):
            source_hits += 1

        expected_tokens = tokens(expected_text)
        answer_tokens = tokens(answer_text)
        question_tokens = tokens(question_text)
        citation_tokens = tokens(citation_text)
        answer_relevance = 0.7 * coverage(expected_tokens, answer_tokens) + 0.3 * coverage(question_tokens, answer_tokens)
        citation_support = coverage(answer_tokens, citation_tokens)
        answer_relevance_scores.append(answer_relevance)
        citation_support_scores.append(citation_support)

        score = score_map.get(sample["sample_key"], {})
        if not score.get("pass_fail", False):
            reason = []
            if float(score.get("faithfulness_score") or 0.0) < 0.5:
                reason.append("low faithfulness")
            if float(score.get("context_recall_score") or 0.0) < 0.4:
                reason.append("low context recall")
            if answer_relevance < 0.4:
                reason.append("low answer relevance proxy")
            failed_rows.append(
                {
                    "sample_key": sample["sample_key"],
                    "question": question_text,
                    "expected_source": expected_source,
                    "faithfulness": score.get("faithfulness_score"),
                    "context_recall": score.get("context_recall_score"),
                    "answer_relevance_proxy": round(answer_relevance, 4),
                    "citation_support": round(citation_support, 4),
                    "first_citation": citation_labels[0] if citation_labels else "",
                    "reason": ", ".join(reason) if reason else "threshold failure",
                }
            )

    return {
        "summary": {
            **score_summary,
            "average_answer_relevance_proxy": round(mean(answer_relevance_scores), 4) if answer_relevance_scores else 0,
            "average_citation_support": round(mean(citation_support_scores), 4) if citation_support_scores else 0,
            "citation_presence_rate": round(citation_presence / len(results), 4) if results else 0,
            "source_hit_at_5": source_hits,
            "source_hit_at_5_rate": round(source_hits / len(results), 4) if results else 0,
            "retrieval_latency_ms_average": round(mean(retrieval_latencies), 2) if retrieval_latencies else 0,
            "retrieval_latency_ms_p50": percentile(retrieval_latencies, 0.5),
            "retrieval_latency_ms_p95": percentile(retrieval_latencies, 0.95),
            "end_to_end_latency_ms_average": round(mean(end_to_end_latencies), 2) if end_to_end_latencies else 0,
            "end_to_end_latency_ms_p50": percentile(end_to_end_latencies, 0.5),
            "end_to_end_latency_ms_p95": percentile(end_to_end_latencies, 0.95),
            "model_latency_ms_average": round(mean(model_latencies), 2) if model_latencies else 0,
            "model_latency_ms_p50": percentile(model_latencies, 0.5),
            "model_latency_ms_p95": percentile(model_latencies, 0.95),
        },
        "failed_samples": failed_rows,
    }


def write_markdown(report: dict, output_path: Path) -> None:
    summary = report["summary"]
    lines = [
        "# Golden QA Evaluation Analysis",
        "",
        "## Summary",
        "",
        f"- Samples: {summary['sample_count']}",
        f"- Passed: {summary['passed']}",
        f"- Failed: {summary['failed']}",
        f"- Pass rate: {summary['pass_rate']}",
        f"- Average faithfulness: {summary['average_faithfulness_score']}",
        f"- Average context recall: {summary['average_context_recall_score']}",
        f"- Average answer relevance proxy: {summary['average_answer_relevance_proxy']}",
        f"- Average citation support: {summary['average_citation_support']}",
        f"- Citation presence rate: {summary['citation_presence_rate']}",
        f"- Source hit at 5: {summary['source_hit_at_5']} ({summary['source_hit_at_5_rate']})",
        f"- Retrieval latency average/p50/p95 ms: {summary['retrieval_latency_ms_average']} / {summary['retrieval_latency_ms_p50']} / {summary['retrieval_latency_ms_p95']}",
        f"- End-to-end latency average/p50/p95 ms: {summary['end_to_end_latency_ms_average']} / {summary['end_to_end_latency_ms_p50']} / {summary['end_to_end_latency_ms_p95']}",
        f"- Model latency average/p50/p95 ms: {summary['model_latency_ms_average']} / {summary['model_latency_ms_p50']} / {summary['model_latency_ms_p95']}",
        "",
        "## Failed Samples",
        "",
        "| Sample | Faithfulness | Context recall | Answer relevance | Citation support | Reason |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in report["failed_samples"]:
        lines.append(
            f"| {item['sample_key']} | {item['faithfulness']} | {item['context_recall']} | "
            f"{item['answer_relevance_proxy']} | {item['citation_support']} | {item['reason']} |"
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze Eduground golden QA result files beyond pass/fail scores.")
    parser.add_argument("results", type=Path)
    parser.add_argument("scores", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()

    report = summarize(args.results, args.scores)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        write_markdown(report, args.markdown_output)
    print(json.dumps(report["summary"], indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
