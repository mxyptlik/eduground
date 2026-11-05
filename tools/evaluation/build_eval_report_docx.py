"""Build the Word report for the Eduground evaluation (for the manuscript).

Reads evaluation/reports/final-results.json and writes
evaluation/reports/Eduground-Evaluation-Report.docx with:
  - dataset and evaluation design
  - models and tools
  - metric definitions and formulas (deterministic + RAGAS)
  - how each metric was implemented
  - full results (overall and per-course tables)
  - findings, caveats, reproducibility, limitations

Run from the repository root:

    ./.venv/Scripts/python.exe tools/evaluation/build_eval_report_docx.py
"""

from __future__ import annotations

import json
from pathlib import Path

from docx import Document
from docx.shared import Pt, RGBColor

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "evaluation/reports/final-results.json"
OUT = ROOT / "evaluation/reports/Eduground-Evaluation-Report.docx"

DET_KEYS = [
    ("id_recall_at_k@1", "id_recall@1"),
    ("id_recall_at_k@3", "id_recall@3"),
    ("id_recall_at_k@5", "id_recall@5"),
    ("id_recall_at_k@10", "id_recall@10"),
    ("precision_at_k@5", "precision@5"),
    ("hit_at_k@5", "hit@5"),
    ("mrr@1", "MRR"),
    ("ndcg_at_k@5", "nDCG@5"),
]
RAGAS_KEYS = [
    ("faithfulness", "Faithfulness"),
    ("answer_relevancy", "Answer Relevancy"),
    ("context_recall", "Context Recall"),
    ("context_precision", "Context Precision"),
    ("factual_correctness_f1", "Factual Correctness F1"),
]


def add_table(doc: Document, headers: list[str], rows: list[list[str]], font_size: int = 9) -> None:
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for i, header in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = ""
        run = cell.paragraphs[0].add_run(header)
        run.bold = True
        run.font.size = Pt(font_size)
    for row in rows:
        cells = table.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = ""
            run = cells[i].paragraphs[0].add_run(str(value))
            run.font.size = Pt(font_size)
    doc.add_paragraph()


def bullet(doc: Document, text: str) -> None:
    doc.add_paragraph(text, style="List Bullet")


def main() -> int:
    rep = json.loads(RESULTS.read_text(encoding="utf-8"))
    det = rep["deterministic"]
    ragas = rep["ragas"]
    overall_nr = det["condition_no_rerank"]
    overall_rr = det["condition_rerank"]
    per_nr = det["per_course_no_rerank"]
    ragas_overall = ragas["overall"]
    ragas_per = ragas["per_course"]
    budget = rep["budget"]

    doc = Document()
    doc.core_properties.title = "Eduground RAG Evaluation - Technical Report"

    # ---------------------------------------------------------------- title
    title = doc.add_heading("Eduground RAG Evaluation - Technical Report", level=0)
    subtitle = doc.add_paragraph()
    run = subtitle.add_run(
        "Curriculum-grounded tutoring system - retrieval and answer quality evaluation\n"
        "17 courses | 166 questions | deterministic retrieval metrics + RAGAS (LLM-judged) metrics"
    )
    run.italic = True
    doc.add_paragraph("Generated: " + rep["generated_at_utc"] + " (UTC)")
    doc.add_paragraph(
        "Source of record: evaluation/reports/final-results.json (machine record with SHA-256 hashes of every "
        "scoring table). This document mirrors that record for reading and manuscript editing."
    )

    # ------------------------------------------------------------ executive
    doc.add_heading("1. Executive summary", level=1)
    bullet(doc, "Evaluation set: 166 questions across 17 courses, every item source-verified during authoring "
                "and accepted by independent human review (166/166).")
    bullet(doc, "Retrieval is strong: 88.8% of required evidence chunks appear in the top 5 (97.4% in the top 10); "
                "92.2% of questions have at least one required chunk in the top 5; nDCG@5 = 0.728.")
    bullet(doc, "RAGAS: context recall 0.925, context precision 0.823, answer relevancy 0.834, faithfulness 0.728, "
                "factual correctness F1 0.246 (a strict, claim-level lower bound - see section 6.3).")
    bullet(doc, "An LLM reranker (openai/gpt-4.1-mini) changed retrieval by at most 0.006 on any metric - the "
                "simpler no-rerank pipeline is reported as equivalent.")
    bullet(doc, "Coverage: 828 of 830 judge steps completed; the 2 remaining are a known output-cap failure "
                "class (judge truncation) on two faithfulness items.")
    bullet(doc, "Total evaluation spend: $%.2f against a $%s budget cap." % (budget["settled_usd"], budget["cap_usd"]))

    # ------------------------------------------------------ dataset & design
    doc.add_heading("2. Dataset and evaluation design", level=1)
    doc.add_paragraph(
        "Corpus. 17 course notebooks were ingested into a local corpus (SQLite mirror + Qdrant vector index). "
        "208 complete sources produced 1,360 indexed text chunks; 14 files were excluded before indexing "
        "(9 parse timeouts, 5 scanned/image-only documents). Retrieval runs against the Qdrant collection "
        "'eduground_chunks'."
    )
    doc.add_paragraph(
        "Questions. 166 questions with expert reference answers were authored from the course materials "
        "(10 per course; 8 each for Computer-Graphics-and-Animation and MAT212), then independently reviewed: "
        "every question, reference, and quoted source passage was accepted by the human reviewer. Reviewed "
        "datasets were frozen per course with SHA-256 hashes and provenance records under "
        "evaluation/releases/<course>/."
    )
    doc.add_paragraph(
        "Answer collection. For each question the system retrieves evidence chunks, composes a grounded prompt, "
        "and generates an answer. Two retrieval conditions were collected: no_rerank (plain vector retrieval) "
        "and rerank (same candidates re-ordered by an LLM reranker). RAGAS judging was performed on the "
        "no_rerank condition; deterministic metrics were computed for both."
    )
    doc.add_paragraph(
        "Scoring pipeline. Deterministic retrieval metrics are computed directly from ranked chunk-ID lists "
        "against the reviewer-labelled evidence IDs (no model involved). RAGAS metrics are computed per item "
        "from the recorded tuple (question, retrieved chunks, generated answer, reference answer) using a "
        "pinned LLM judge and pinned embeddings."
    )

    # ----------------------------------------------------------- models
    doc.add_heading("3. Models and tools", level=1)
    doc.add_paragraph("Each pipeline role uses a pinned model; no role shares a model with another role.")
    add_table(
        doc,
        ["Role", "Model", "What it processed"],
        [
            ["Author", "moonshotai/kimi-k2.6",
             "Wrote the 166 candidate questions and reference answers from source passages (then human-reviewed)."],
            ["Answer (system under test)", "qwen/qwen3.7-flash",
             "Generated every scored answer from the retrieved evidence + system prompt."],
            ["Judge (evaluation)", "deepseek/deepseek-v4.1-flash",
             "All RAGAS verdicts: claim entailment, chunk usefulness, question generation, NLI scoring."],
            ["Embeddings", "openai/text-embedding-3-large",
             "Indexed chunks; also the answer-relevancy similarity embeddings."],
            ["Reranker (rerank condition only)", "openai/gpt-4.1-mini",
             "Re-ordered retrieved candidates for the rerank comparison arm."],
        ],
    )
    doc.add_paragraph(
        "Tools and infrastructure: Python 3.14 local harness; Qdrant vector database (local); SQLite for the "
        "corpus mirror, per-run journals, and the shared budget ledger; RAGAS 0.4.3 (collections API); "
        "OpenAI Python SDK + Instructor via OpenRouter; a custom budget-guarded HTTP transport that reserves "
        "a worst-case USD bound before every call, records usage, and blocks dispatch on unverifiable spend."
    )
    doc.add_paragraph(
        "Answer-generation prompt: the system prompt is the versioned tutor prompt bundle "
        "(packages/prompts, active v2: tutor + guardrails + teaching policy + citation policy) plus an inline "
        "citation-marker instruction. The user message wraps retrieved evidence as untrusted reference data "
        "inside <retrieved_evidence> tags followed by <learner_question>. The exact prompt instance for every "
        "answer is stored in each run's artifacts/answers.jsonl (prompt_messages)."
    )
    doc.add_paragraph(
        "Judge configuration: conversational/answer roles and the judge are called with usage accounting "
        "(OpenRouter usage.include), judge output capped at 12,288 tokens, concurrency 8 in scoring runs."
    )

    # -------------------------------------------- deterministic metrics
    doc.add_heading("4. Deterministic retrieval metrics - definitions, formulas, implementation", level=1)
    doc.add_paragraph(
        "These metrics are model-free: they compare the ranked retrieval list against the reviewer-labelled "
        "evidence IDs of each question ('gold chunks'). Each standalone gold ID is one required evidence unit; "
        "IDs inside an alternative group would be interchangeable ways to satisfy one unit (no alternative "
        "groups occur in the frozen data). A duplicated chunk ID consumes its rank slot but only its first "
        "occurrence can earn credit. All values are 0-1 (higher is better), averaged over applicable questions "
        "(all 166 here)."
    )
    add_table(
        doc,
        ["Metric (k used)", "Formula", "What it measures"],
        [
            ["id_recall@k (1,3,5,10)",
             "found required units in top-k / total required units",
             "Coverage: how much of the question's required evidence is retrieved."],
            ["precision@k (5)",
             "credited required chunks in top-k / unique chunks returned in top-k",
             "Signal-to-noise: how much of the retrieved window is actually required evidence."],
            ["hit@k (5)",
             "1 if any required unit in top-k else 0; averaged",
             "Whether retrieval surfaced anything required for the question."],
            ["MRR",
             "mean over questions of 1 / rank(first required chunk)",
             "How high the first required chunk ranks."],
            ["nDCG@k (5)",
             "DCG@k / iDCG@k, where DCG = sum over credited hits of (2^grade - 1) / log2(rank + 1)",
             "Rank-weighted relevance: required chunks ranked higher score better (ideal ordering in the denominator)."],
        ],
    )
    doc.add_paragraph(
        "Implementation notes: grades default to 1.0 (binary relevance) in this evaluation; precision counts "
        "each evidence unit at most once and divides by the number of unique returned IDs; unanswerable items "
        "or items without positive labels yield N/A (not zero) and are excluded from the macro average. "
        "The same metric implementation was re-run and produced byte-identical results "
        "(evaluation/reports/deterministic-snapshot.json)."
    )

    # --------------------------------------------------- RAGAS metrics
    doc.add_heading("5. RAGAS metrics - definitions, formulas, implementation", level=1)
    doc.add_paragraph(
        "All five metrics consume the recorded tuple (question, retrieved chunks, generated answer, reference "
        "answer); each metric uses a different slice of it. Scores are 0-1, higher is better; means are reported "
        "over completed items."
    )
    add_table(
        doc,
        ["Metric", "Inputs", "How it is computed"],
        [
            ["Faithfulness", "answer + retrieved chunks",
             "Judge decomposes the answer into atomic statements; each statement receives an NLI verdict "
             "('can it be directly inferred from the retrieved evidence?'). Score = supported statements / "
             "total statements. This is the hallucination check."],
            ["Answer Relevancy", "question + answer",
             "The LLM generates 3 questions the answer could plausibly be answering (strictness=3); those and "
             "the original question are embedded (text-embedding-3-large); score = mean cosine similarity. "
             "Detects evasive/unfocused answers; truth-neutral."],
            ["Context Recall", "reference + retrieved chunks",
             "The reference answer is split into claims; each is checked for attribution to the retrieved "
             "chunks. Score = attributed claims / total reference claims. Upper bound on achievable answer "
             "completeness."],
            ["Context Precision", "question + reference + retrieved chunks",
             "Every retrieved chunk receives a judge verdict ('was this chunk useful in arriving at the "
             "reference answer?', 0/1). Verdicts are combined with average precision (rank-weighted): useful "
             "chunks appearing earlier count more."],
            ["Factual Correctness F1", "answer + reference",
             "Claims are extracted from the answer and from the reference. Precision = answer claims entailed "
             "by the reference / all answer claims; Recall = reference claims covered by the answer / all "
             "reference claims; score = F1 = 2PR/(P+R)."],
        ],
    )
    doc.add_paragraph(
        "Implementation notes: metric classes were RAGAS 0.4.3 collections implementations "
        "(Faithfulness, AnswerRelevancy, ContextRecall, ContextPrecisionWithReference, FactualCorrectness "
        "mode='f1'), driven by the pinned deepseek judge. Items that are unanswerable or lack contexts/reference "
        "are recorded as N/A rather than zero. RAGAS returns component values rounded to two decimals for the "
        "claims-based metrics."
    )

    # ------------------------------------------------------------ results
    doc.add_page_break()
    doc.add_heading("6. Results", level=1)

    doc.add_heading("6.1 Deterministic retrieval - overall (166 items)", level=2)
    rows = []
    for key, label in DET_KEYS:
        a, b = overall_nr.get(key), overall_rr.get(key)
        rows.append([label, "%.4f" % a if a is not None else "-", "%.4f" % b if b is not None else "-"])
    add_table(doc, ["Metric", "no_rerank", "rerank (comparison)"], rows)
    doc.add_paragraph(
        "The rerank arm differs by at most 0.006 on every metric; the reranker adds no measurable value, so "
        "the simpler no_rerank pipeline is the reportable condition."
    )

    doc.add_heading("6.2 Deterministic retrieval - per course (no_rerank)", level=2)
    rows = []
    for cid in sorted(per_nr):
        v = per_nr[cid]
        cells = [cid]
        for key, _ in DET_KEYS:
            x = v.get(key)
            cells.append("%.3f" % x if x is not None else "-")
        rows.append(cells)
    add_table(doc, ["Course"] + [label for _, label in DET_KEYS], rows, font_size=8)

    doc.add_heading("6.3 RAGAS - overall", level=2)
    status = ragas["status_counts"]
    rows = []
    for key, label in RAGAS_KEYS:
        entry = ragas_overall.get(key, {})
        rows.append([label, "%.4f" % entry.get("mean", 0.0), str(entry.get("n", 0))])
    add_table(doc, ["Metric", "Mean", "n completed"], rows)
    doc.add_paragraph(
        "Status counts across all judge steps: %s. The two missing faithfulness items failed with "
        "IncompleteOutputException (the judge's structured output exceeded its token cap on those inputs) - a "
        "known, fixable failure class, not a methodological gap." % json.dumps(status)
    )

    doc.add_heading("6.4 RAGAS - per course", level=2)
    rows = []
    for cid in sorted(ragas_per):
        v = ragas_per[cid]
        cells = [cid]
        for key, _ in RAGAS_KEYS:
            if key in v and v[key] is not None:
                cells.append("%.3f (n=%d)" % (v[key][0], v[key][1]))
            else:
                cells.append("-")
        rows.append(cells)
    add_table(doc, ["Course"] + [label for _, label in RAGAS_KEYS], rows, font_size=8)

    doc.add_heading("6.5 Interpretation of Factual Correctness F1 (important for the manuscript)", level=2)
    doc.add_paragraph(
        "Reported value: 0.246 (n=166). This metric is a strict, claim-level lower bound on factual agreement "
        "and should not be read as 'the system is 75% wrong'. Evidence:"
    )
    bullet(doc, "Sanity probe: the metric returns 1.0 when the answer is identical to the reference - the "
                "pipeline is functioning.")
    bullet(doc, "Worked example (Project-Management): the question asks for the four processes of Project Human "
                "Resource Management; the answer lists all four correctly plus one-sentence descriptions of each. "
                "The judge credits only the single 'core list' claim (1 true positive) and marks the five "
                "descriptive claims as false positives because the one-line reference does not contain them - "
                "F1 collapses even though the answer is correct and complete.")
    bullet(doc, "Replay of the same item (same texts, same judge) scored 0.29 where the recorded run scored 0.00: "
                "single NLI verdict draws flip items between 0.00 and ~0.3. 87 of 166 items scored exactly 0.00 "
                "(nothing between 0 and 0.10), showing an all-or-nothing pattern on terse references.")
    bullet(doc, "Root cause: the reference answers are terse grading keys while the system answers are full "
                "explanations (3-10x longer); every correct-but-extra claim counts against precision, and strict "
                "inference rejects paraphrase. Options for the paper: report F1 with this caveat (recommended), "
                "report its precision and recall components separately, and/or add a human audit of the "
                "zero-scored items.")

    doc.add_heading("6.6 Cost and coverage", level=2)
    role_rows = sorted(budget["by_role_usd"].items())
    add_table(doc, ["Role", "Settled USD"],
              [[role, "$%.4f" % amount] for role, amount in role_rows] +
              [["TOTAL", "$%.2f" % budget["settled_usd"]]])
    doc.add_paragraph(
        "Coverage: 828 of 830 judge steps completed. Budget cap: $%s. A ledger reconciliation note (expired "
        "API-key window where failed 401 attempts had been conservatively charged at their reserved bound) is "
        "recorded in the machine result." % budget["cap_usd"]
    )

    # ------------------------------------------------------ reproducibility
    doc.add_heading("7. Reproducibility and artifacts", level=1)
    bullet(doc, "Machine record: evaluation/reports/final-results.json (overall + per-course metrics, status "
                "counts, budget, SHA-256 hashes of 267 scoring artifacts).")
    bullet(doc, "Human-readable board: evaluation/reports/final-report.md.")
    bullet(doc, "Per-run evidence: evaluation/runs/<run>/artifacts/ - answers.jsonl (exact prompts + answers), "
                "retrievals.jsonl (ranked candidates), scoring/ (ragas.json, retrieval.json, citations.json).")
    bullet(doc, "Frozen datasets: evaluation/releases/<course>/dataset.json + dataset.sha256 + provenance.json.")
    bullet(doc, "Budget ledger: evaluation/pilot/budget.sqlite3 (per-call reservations, settled costs, by role).")
    bullet(doc, "Completing/refreshing the RAGAS judging + report: "
                "./.venv/Scripts/python.exe tools/evaluation/complete_coverage.py")
    bullet(doc, "Exporting results for the human review page: "
                "./.venv/Scripts/python.exe tools/evaluation/review/export_eval_review.py")

    # ---------------------------------------------------------- limitations
    doc.add_heading("8. Limitations", level=1)
    bullet(doc, "Development-scale set: 166 questions over 17 courses; conclusions about individual courses rest "
                "on 8-10 items each.")
    bullet(doc, "LLM-judged metrics carry judge variance; item-level values for the claims-based metrics can move "
                "between draws (documented for Factual Correctness).")
    bullet(doc, "Deterministic metrics depend on the reviewer-labelled evidence IDs as the definition of gold; "
                "chunks adjacent to labelled evidence earn no credit.")
    bullet(doc, "Two faithfulness items remain unscored (judge output cap); they are flagged rather than "
                "imputed.")
    bullet(doc, "Reranker comparison covers retrieval metrics only; RAGAS was not run on the rerank condition.")

    # ------------------------------------------------------------ appendix
    doc.add_page_break()
    doc.add_heading("Appendix A. Metric quick reference", level=1)
    add_table(
        doc,
        ["Family", "Metrics", "Judged by"],
        [
            ["Deterministic retrieval", "id_recall@1/3/5/10, precision@5, hit@5, MRR, nDCG@5",
             "No model - ranked chunk IDs vs reviewer-labelled evidence IDs."],
            ["RAGAS (generation + retrieval quality)",
             "Faithfulness, Answer Relevancy, Context Recall, Context Precision, Factual Correctness F1",
             "deepseek/deepseek-v4.1-flash judge (+ text-embedding-3-large for relevancy)."],
        ],
    )

    doc.add_heading("Appendix B. Worked F1 example (for context in the manuscript)", level=1)
    doc.add_paragraph("Question: What are the four processes included in Project Human Resource Management?")
    doc.add_paragraph("Reference answer (grading key): Planning human resource management, acquiring the project "
                      "team, developing the project team, and managing the project team.")
    doc.add_paragraph("System answer (abridged): lists all four processes, each with a one-sentence description, "
                      "plus a closing sentence about how the processes work together.")
    doc.add_paragraph("Judge outcome: 1 of 6 answer claims credited (the core list); 5 descriptive claims marked "
                      "as not inferable from the one-line reference. Reference claims covered: 1 of 1. "
                      "Resulting F1 on replay: 0.29; recorded run: 0.00 (single-draw variance).")

    doc.save(str(OUT))
    print("wrote %s | %d bytes" % (OUT, OUT.stat().st_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
