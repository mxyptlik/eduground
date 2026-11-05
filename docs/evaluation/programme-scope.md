# Evaluation programme: scope, formulas, and independently selectable work

## Purpose and boundaries

Measure answer quality, retrieval, reranking, citations, latency, external API cost, load, scalability, document handling, and generalisation across 17 courses. RAGAS is the quality-evaluation framework, including explicitly labelled custom citation metrics. Deterministic retrieval scores and operational telemetry are recorded alongside it; they are not model-judged substitutes for actual measurements.

This document preserves the agreed programme, updated for 17 courses. The implementation backlog is separate so tasks can be selected independently. Dropping a module drops its conclusions, not unrelated experiments. Shared contracts, source identity, run identity, and outcome accounting are mandatory foundations.

Not included: production deployment, autoscaling implementation, model fine-tuning, a student learning-outcomes study, adding document formats, or automatically fixing every weakness found. Instrumentation and controlled experiment hooks are included; broad product changes require a separate task.

## Model roles and independence

| Role | Model family | Provider | Rule |
|---|---|---|---|
| Answer generation | Gemini | Gemini API | Same pinned model across paired configurations |
| Question/reference authoring | Kimi | OpenRouter | Source-bounded drafts, never self-approved |
| RAGAS judge | DeepSeek | OpenRouter | Independent of author and answer model; fixed judge across comparisons |
| Retrieval embeddings | To pin | Explicit adapter | Same model/dimensions for ingestion and queries |
| Relevancy embeddings | To pin | Explicit adapter | Separate recorded evaluation setting |
| Reranker | To select | Existing OpenRouter adapter is a candidate | Do not implicitly assign the judge model |

Pin exact IDs and routing settings after capability probes. Disable silent model-family fallback in controlled experiments. Record actual provider/model as well as requested model. Human review remains necessary. A project author reviewing model output is human review independent of the model, not necessarily an independent external academic reviewer.

## Datasets and configurations

One frozen source-only evaluation notebook per course. Separate development and held-out question sets. Review references, exact supporting passages, locators, alternative evidence, question type, difficulty, and answerability. Preserve rejected/flagged items and all review revisions. Freeze checksums, source versions, extraction/chunking settings, and embedding configuration.

Pilot proposal: two contrasting courses, 10-20 reviewed questions each. Scale to all 17 only after quality, time, and cost checks. A provisional 100 questions per course implies 1,700 primary items; this is a workload scenario, not a fixed requirement. Report macro averages across courses and micro averages across questions, plus all per-course results. Prevent near-duplicate questions or shared source extracts leaking across development/test splits. Do not tune on the held-out course slices.

| ID | Configuration | What it establishes |
|---|---|---|
| E01 | Same answer model, no retrieval | Existing model knowledge; relevancy and correctness only |
| E02 | Retrieval without reranking | Retrieval contribution |
| E03 | Retrieval with reranking | Main system result and reranking contribution |
| E04 | Reviewer-selected evidence supplied directly | Diagnostic generation performance given known evidence |

Fresh chat/session for each question and configuration. E02/E03 use the same candidate pool and final evidence budget. Save candidates, rankings, reranking success/failure, exact final context, model input, and response. The no-retrieval prompt must permit a knowledge-based answer rather than require evidence and force refusal; document that minimal policy difference. E04 is an optional subset. Grounding/retrieval/citation scores are N/A for E01. Factual correctness is measured on the generated answer after reranking for E03 and also on baseline answers for paired comparisons.

## Answer and context metrics

Notation: q = question; a = answer; r = reviewed reference; C = exact final context; k = retrieval cutoff; E = embedding function. A score is not a probability of a correct answer. Pin RAGAS version, prompts, claim decomposition settings, and denominator conventions.

### Q01 Answer relevancy

Generate m possible questions g_i from a and compare their embeddings with q:

`AR = (1/m) * sum_i cosine(E(q), E(g_i))`

Record m, embedding model, and non-answer handling in the installed RAGAS implementation. Semantic alignment does not establish correctness. Cosine similarity is not mathematically restricted to [0,1].

### Q02 Faithfulness

`F = supported answer claims / assessable answer claims`

Support must come from C, not the judge's prior knowledge. An incomplete answer can still be faithful. No assessable claims is N/A with an explicit reason, not an automatic perfect score.

### Q03 RAGAS context precision

Let v_i be 1 for a useful chunk and 0 otherwise:

`P@i = (sum_{j=1..i} v_j) / i`

`CP@k = sum_{i=1..k}(P@i * v_i) / sum_{i=1..k} v_i`

This is rank-sensitive average precision over useful returned chunks, not ordinary Precision@k. Use 0 when no returned chunk is useful for an answerable question; record the library convention. Score original and reranked order separately.

### Q04 Context recall

`CR = reference claims supported by C / assessable reference claims`

Measures necessary information available to the generator, not whether it used it. Unanswerable cases have a separate refusal protocol.

### Q05 Factual correctness

TP = answer claims supported by r; FP = answer claims unsupported by r; FN = required reference claims absent from a.

`P_f = TP/(TP+FP)`

`R_f = TP/(TP+FN)`

`F1_f = 2*P_f*R_f/(P_f+R_f)`

Report precision, recall, and F1. FP here means unsupported by the reference, not necessarily false in the world. References must cover legitimate correct alternatives. Empty responses and missing references receive explicit statuses and predefined treatment, never silent omission.

## Retrieval metrics independent of model judgments

G_q = reviewed relevant evidence units; R_q,k = returned evidence units up to k; N = questions. Label alternative valid evidence and evidence groups to avoid rewarding duplicated overlapping chunks.

| ID | Metric | Formula |
|---|---|---|
| R01 | Recall@k | `size(G_q intersect R_q,k) / size(G_q)` |
| R02 | Precision@k | `size(G_q intersect R_q,k) / size(R_q,k)`; empty returned set = 0 for answerable items |
| R03 | Hit rate@k | `mean_q indicator(G_q intersect R_q,k is nonempty)` |
| R04 | MRR | `mean_q 1/rank_of_first_relevant`; no hit = 0 |
| R05 | nDCG@k | `DCG@k / ideal_DCG@k` |
| R06 | Reranking uplift | Paired change in each metric before/after reranking |

`DCG@k = sum_{i=1..k} (2^relevance_i - 1)/log2(i+1)`

Propose k = 1,3,5,10 where candidate depth permits. No relevant gold evidence means N/A, not division by zero. Do not mistake current one-reference-chunk annotations for exhaustive relevance labels. Candidate recall bounds what reranking can recover.

## Citation metrics

| ID | Metric | Formula/definition |
|---|---|---|
| C01 | Validity | `resolvable citation targets / all citation targets` |
| C02 | Claim mapping | Explicit association of answer claims and attached citations; prerequisite for semantic citation scores |
| C03 | Correctness | `supporting claim-citation links / assessed claim-citation links` |
| C04 | Sufficiency | `evidence-requiring claims fully supported by their attached citations / all evidence-requiring answer claims` |
| C05 | Required-answer evidence coverage | `required reference facts stated and adequately cited / all required reference facts` |
| C06 | Judge validation | Agreement/disagreement with human-labelled examples |

C03 permits an individual citation to contribute legitimate support; C04 asks whether attached passages jointly establish the entire claim. Use only attached cited passages, not all retrieval results. Mark partial support, no support, and contradiction separately. Missing/invalid citations cannot provide support. Exclude non-factual text. Citation-free factual answers have sufficiency 0 but link correctness N/A; report both with counts. Multiple citations can jointly support one claim. Required-answer coverage prevents a terse answer that omits required facts from looking fully satisfactory.

Implement C03-C05 as documented custom RAGAS metrics and retain rationales; do not call them built-in RAGAS citation metrics.

## Refusal module

| ID | Work | Formula |
|---|---|---|
| A01 | Record legitimate empty retrieval | Outcome is not automatically a collector failure |
| A02 | Correct refusal rate | `appropriate refusals on unanswerable questions / unanswerable questions` |
| A03 | False refusal rate | `unnecessary refusals on answerable questions / answerable questions` |

Human reviewer confirms absence of required evidence in the frozen corpus; failure to retrieve is not proof of unanswerability.

## Latency module

| ID | Measurement | Definition |
|---|---|---|
| L01 | End-to-end time | `response complete - request start` |
| L02 | TTFT | `first actual token received - request start`; not status/source events |
| L03 | Stage time | embedding, vector search, reranking, prompt preparation, generation, persistence |
| L04 | Cache conditions | cold/warm and hit/miss recorded independently |
| L05 | Ingestion time | `indexed - upload accepted` |
| L06 | Distributions | p50/p95/p99 where sample counts support them |

Use monotonic timers for intervals. Concurrent span durations do not necessarily sum to elapsed time. Internal-workflow latency and HTTP/client latency are separate. Judge calls happen after measurement. Buffered output is not native streaming TTFT.

## External API cost module

| ID | Work | Result |
|---|---|---|
| K01 | Usage capture | request/model/provider identity, token/request units, cache, reasoning tokens if exposed, retry/fallback |
| K02 | Dated prices | currency, unit, effective date, source, actual vs estimated |
| K03 | Ingestion | OCR/extraction and document embeddings |
| K04 | Serving | query embedding, reranking, generation, failed/retried attempts |
| K05 | Evaluation | authoring, RAGAS judge, relevance embeddings |
| K06 | Projections | explicit volumes and assumptions, not measured production bills |

`C_call = (input_tokens*input_price + output_tokens*output_price)/1,000,000`

Apply separate terms for cached/other billable units; provider costs take precedence where reliable. Avoid double-counting reasoning tokens already included in billed output. `C_query = sum attributable call costs`. `Cost_per_success = total serving cost including failures / successful answers`. Unknown usage stays unknown, not zero. API charges and infrastructure charges remain separate.

## Load and scalability

P01 single-user baseline; P02 sustained chat; P03 ingestion; P04 mixed traffic; P05 stress; P06 spike; P07 soak; P08 post-hoc RAGAS quality under load. Each is removable.

Start with a pilot, then consider 1/5/10/25/50 concurrent users. Specify arrival rate, think time, duration, workload, cache state, corpus, resources, and test identity. Real-provider runs need bounded spend; stubbed-provider runs isolate infrastructure and must be labelled separately.

`Throughput = completed requests / elapsed seconds`

`Error_rate = failed requests / attempted requests`

Measure queue depth, CPU/memory, DB connections, worker throughput, timeouts, and provider rate limits. Establish service targets before final tests, not after seeing results.

S01 API capacity comparison; S02 worker capacity comparison; S03 corpus growth; S04 capacity boundaries.

`Speedup(n) = throughput(n resource units) / throughput(1 resource unit)`

`Efficiency(n) = Speedup(n)/n`

Use equivalent workloads and latency/error constraints. Report actual hardware and bottlenecks. Laptop concurrency is not proof of production horizontal scalability. Performance testing must use separate user/notebook identities where making multi-user isolation claims.

## Document types

T01 verify support matrix; T02 text PDF; T03 scanned PDF; T04 tables/multi-column; T05 DOCX/PPTX; T06 equations/code/diagrams; T07 matched-format corpus. Each experiment is removable. Existing parsers recognise PDF, DOCX, PPTX, MD/Markdown, TXT; this does not establish accurate understanding of all content inside them.

`CER = (substitutions + deletions + insertions) / reference characters`

WER uses reference words. Use human transcripts for a subset and separately assess structure/reading order, locators, extraction completeness, ingestion, retrieval, and final answers. Unsupported formats are limitations, not automatically added features.

## Variation and reporting

V01 mean/median/std/quartiles and item values; V02 uncertainty; V03 paired comparisons; V04 repeated generation; V05 repeated judging; V06 human agreement; V07 course-specific results; V08 failure taxonomy.

`Mean = sum(item scores)/N`

`Delta_q = score_q,reranked - score_q,baseline`

Bootstrap paired questions, stratify by course as appropriate, and do not treat repeated judgments as independent questions. Report confidence intervals, distributions, and win/tie/loss counts. For 17 courses show both equal-course macro and question-weighted micro summaries. No arbitrary all-in-one Eduground score. Account for every attempted item; do not hide failed items through a successful-only average. Retain actual judge decisions and source evidence for audit.

## Delivery stages

1. Foundation and reviewed pilot dataset.
2. Selectable metric/telemetry facilities and model capability probes.
3. Small pilot; correct measurement problems.
4. Freeze protocol, datasets, prompts, prices, judge, and thresholds.
5. Full quality experiments and independently selected operational experiments.
6. Separate domain reports, provenance bundle, limitations, and implementation follow-ups.

Source references: [RAGAS metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/), [custom scoring](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/general_purpose/), [RAGAS cost tracking](https://docs.ragas.io/en/latest/howtos/applications/_cost/), [k6 testing](https://grafana.com/docs/k6/latest/testing-guides/automated-performance-testing/).
