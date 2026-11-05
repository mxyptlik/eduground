# Repository-specific implementation plan

Prepared 2026-09-30 after reading the current code. Task tables specify target acceptance evidence, not current completion. See [progress.md](progress.md) for the implementation status and offline test results.

## Current code findings

| Existing code | Observed behaviour | Required change or reuse |
|---|---|---|
| `apps/api/app/evaluation/ragas_dataset.py` | Default validator requires exactly 100 samples, one notebook, a single `source_chunk_id`, statuses draft/approved/rejected | Versioned per-course contracts, multiple evidence units, flagged state, review provenance; keep a v1 reader |
| `tools/evaluation/author_ragas_dataset.py` | Instantiates `GeminiLLMProvider`, generates short single-chunk items, checks quote containment | Inject Kimi author, retain source checks, add configurable question blueprints and multi-evidence items |
| `tools/evaluation/approve_ragas_dataset.py` | Source-integrity checks set all rows approved, explicitly admits no human review | Separate integrity from human acceptance; do not use as human approval |
| `tools/evaluation/collect_ragas_inputs.py` | Requires Gemini fallback, blank OpenRouter key, no reranker, dedicated collection; one chat session reused; no-trace output fails | Role-separated config, isolated sessions, four experiment modes, valid refusals, actual context snapshots |
| `tools/evaluation/run_real_ragas.py` | Gemini judge tied to application chat model; two metrics; sequential samples and concurrent metric pair; retries and immutable-by-default outputs | DeepSeek judge adapter, optional metric registry, independent resume units, richer score/usage records |
| `apps/api/app/services/chat.py` and `chat_runtime.py` | Runtime includes conversation history via memory store | New session per item; explicitly separate future multi-turn tests |
| `apps/api/app/workflows/answer_question.py` | Hard-coded vector `top_k=5`; optional reranker on a prefix; reranker errors fall back to original candidates | Configurable candidate/final depth, fair context budget, record effective reranking outcome and untouched candidates |
| `apps/api/app/integrations/rerank/openrouter_adapter.py` | LLM ranks passages through chat completions; passage text truncated to 3,000 chars; missing ranks appended | Record truncation/model/usage and coverage; validate rank output; never describe this as a native cross-encoder without changing it |
| `apps/api/app/integrations/llm/base.py` | Returns strings and discards response usage; OpenRouter may fall back to Gemini; Gemini stream yields a complete generated answer | Optional structured telemetry, explicit role factories, no silent experiment fallback; native TTFT unavailable on current Gemini adapter |
| `apps/api/app/schemas/chat.py`, models and `persist_answer` | Citation objects created for candidate chunks; no explicit answer-claim/marker mapping | Genuine citation association prerequisite; source-list membership is not citation correctness |
| `apps/api/app/policies/citation_policy.py` | Coverage is citation count divided by factual-block count; caller uses one block for candidate-bearing answer | Preserve production behaviour initially; never report this proxy as semantic sufficiency |
| `apps/api/app/api/routes/sources.py`, `services/sources.py` | Signed upload, source registration, job/event endpoints and access checks | Reuse for directory intake, not direct writes to chunks/vector store |
| `apps/worker/app/pipelines/ingestion_pipeline.py`, services | Existing extraction/OCR, normalisation, chunking, embedding/index pipeline | Reuse; instrument outcomes and source versions |
| `apps/worker/app/services/parsing.py` | PDF/DOCX/PPTX/MD/Markdown/TXT parsers; PDF empty-text fallback metadata | Build measured support matrix; recognition is not proof of table/diagram comprehension |
| `apps/api/app/core/observability.py`, worker telemetry, Docker observability overlay | Existing tracing, metrics, monitoring facilities | Extend stage/usage correlation; avoid question/run IDs as high-cardinality Prometheus labels |
| `tests/system/load/common.js`, `chat_path.js` | One bearer token/notebook, fixed VUs, broad thresholds and a repeated question | Isolated user pool, workload files, arrival-rate profiles, explicit provider mode, outcome export |
| `apps/evaluator/app/scorers/ragas_like.py` | Legacy lexical proxy | Keep historical identity; do not rename its results RAGAS |

Existing staged deletions under `chat/` predated this programme work and remain untouched. Previous run evidence remains unchanged.

## Architecture and module boundaries

Keep application ingestion and answering in `apps/api`/`apps/worker`. Put experiment orchestration in a proposed importable `evaluation/program/` package, with thin CLI wrappers under `tools/evaluation/`. This avoids treating the legacy `apps/evaluator/app` lexical scorer as the real RAGAS engine and avoids multiple ambiguous `app` packages in one Python process. The file paths below are proposed unless listed in the inventory above.

```text
evaluation/program/
  contracts.py       # course, evidence, item revision, review, run and score
  config.py          # immutable role/experiment configuration
  corpus.py          # manifests and source snapshot verification
  authoring.py       # Kimi, question blueprints, draft validation
  reviews.py         # revisions, review events, eligibility and freeze
  providers.py       # role factories, actual model/provider identity
  collector.py       # per-item execution and context snapshots
  runner.py          # resumable orchestration and budgets
  metrics/           # RAGAS and deterministic metric plugins
  usage.py           # provider call records; pricing separate from capture
  reports/           # per-domain analysis, paired comparisons, course summaries
  tests/             # pure contract/metric/orchestration tests
tools/evaluation/review/  # current static review workspace
```

Model adapters in the product remain reusable. Inject settings/providers into workflows instead of changing global `.env` between concurrent course runs. New result contracts are additive; keep compatibility wrappers for current string-returning callers. Large text snapshots live in run artifacts, not metric labels.

Review workspace v0 is local static HTML/JS with JSON import/export. A later optional API-backed workspace can reuse the contracts. Do not create a public unauthenticated review endpoint just to simplify the prototype.

## Task semantics

Each ID is a separately reviewable patch or experiment. `Needs` identifies genuine prerequisites. `Human` identifies content decisions; other tasks can proceed without waiting. Unless explicitly marked otherwise, status is planned. Completion requires the listed acceptance evidence, not merely file creation. All tasks also inherit shared rules: no secrets in artifacts, no overwrite of historical runs, typed/schema-validated inputs, recorded failure outcomes.

## M0 — Programme contracts and orchestration

| ID | Task / code target | Needs | Acceptance evidence |
|---|---|---|---|
| M0.1 | Add versioned contracts in `evaluation/program/contracts.py` | None | v1 adapter preserves source data; v2 supports course IDs, variable counts, evidence arrays and answerability |
| M0.2 | Add immutable role/config model in `config.py` | M0.1 | Missing exact model IDs rejected for execution; export redacts credentials; deterministic config hash |
| M0.3 | Define run/artifact layout and atomic writes | M0.1 | Interrupted writes cannot corrupt previous completed artifacts; unique run IDs |
| M0.4 | Add explicit metric/task selection registry | M0.1 | Selecting only faithfulness does not import or require cost/load/citation modules |
| M0.5 | Add resumable per-item/per-metric execution journal | M0.3 | Restart skips completed matching hashes, retries only selected failures, changed config refuses accidental reuse |
| M0.6 | Add run preflight and plan-only CLI | M0.2 | Shows course/item/config counts, missing dependencies, estimated calls and unknown costs without paid calls |
| M0.7 | Add bounded concurrency, quotas and cancellation | M0.5 | Checkpoint on cancellation; per-provider concurrency; retry budgets; user spend ceiling enforced conservatively |

## M1 — Seventeen-course corpus intake

| ID | Task / code target | Needs | Acceptance evidence |
|---|---|---|---|
| M1.1 | Directory inventory CLI, proposed `tools/evaluation/inventory_courses.py` | M0.1; user directory | Read-only inventory lists paths, hashes, suffixes, sizes, duplicates and course mapping; original bytes unchanged |
| M1.2 | Course registry JSON/CSV importer | M0.1 | Unique course IDs; optional notebook IDs; 17 is data, not a hard-coded limit |
| M1.3 | Idempotent batch upload client using source/notebook APIs | M1.1-2 | Same files twice create no duplicate sources; interrupted uploads resumable; normal auth/access checks retained |
| M1.4 | Adopt already-uploaded notebooks | M1.2 | Course/notebook ownership verified, source hashes matched, no re-upload required |
| M1.5 | Ingestion status and extraction-quality inventory | M1.3 or M1.4 | Failed/empty/deleted sources visible, retries distinct; no silent inclusion as indexed evidence |
| M1.6 | Freeze/export course evidence packets | M1.5 | Source checksum/version, extraction/chunk config, full chunks, locators and embedding identity captured |
| M1.7 | Detect cross-course duplicate sources and leakage | M1.6 | Exact duplicate report; near-duplicate candidates require human disposition, not automatic deletion |

Human checkpoint: accept corpus membership and exclusions before authoring. Directory importer and notebook adoption are alternative entry routes, not cumulative requirements.

## M2 — Provider role separation

| ID | Task / code target | Needs | Acceptance evidence |
|---|---|---|---|
| M2.1 | Add explicit Gemini/Kimi/DeepSeek role factories | M0.2 | Author=Kimi/OpenRouter, answer=Gemini/direct, judge=DeepSeek/OpenRouter; separate secrets/settings |
| M2.2 | Add opt-in capability probe CLI | M2.1 | Small bounded calls validate model existence, structured output, context limits; actual IDs and routing saved |
| M2.3 | Add DeepSeek RAGAS judge adapter | M2.1 | Installed RAGAS async interface tested with recorded fixtures and a small live smoke; malformed JSON is a failed judge attempt |
| M2.4 | Add explicit embedding configuration | M0.2 | Ingestion/query dimensions and model match; separate answer-relevancy embedding config |
| M2.5 | Record and constrain fallbacks/routing | M2.1 | Main controlled run fails/flags an unexpected model; never silently swaps judge or answer family |
| M2.6 | Select/configure reranker as separate role | M2.2 | Pinned ID, truncation limits, usage and ranking-output validation; not implicitly DeepSeek |

Exact model versions remain open; family names are not executable model IDs. Reusing OpenRouter credentials for Kimi and DeepSeek is fine; reusing global chat-model settings is not.

## M3 — Authoring and review governance

| ID | Task / code target | Needs | Acceptance evidence |
|---|---|---|---|
| M3.1 | Extract authoring logic from current script and inject Kimi | M1.6, M2.1 | All generated rows pending; quotes validated; role and prompt hashes recorded |
| M3.2 | Add configurable per-course question blueprints | M3.1 | Counts/types/difficulties/splits configurable; insufficient evidence yields a deficit report, not filler |
| M3.3 | Add multi-evidence questions and alternative references | M3.1 | Multiple chunk/source IDs accepted with locator/quote validation |
| M3.4 | Add draft deduplication and split-leakage report | M3.2 | Duplicate candidates flagged; no tuning on held-out sets |
| M3.5 | Separate integrity status from human decision | M0.1 | Automated verifier cannot create human acceptance; legacy automated approvals migrate as not human-reviewed |
| M3.6 | Add append-only review events and revision hashes | M0.1 | Evidence/reference edits invalidate prior acceptance; reviewer/time/before-after revisions retained |
| M3.7 | Import local reviewer bundles | M3.5-6 | Wrong revision, malformed evidence, forged self-consistency and stale hashes rejected or require reconciliation; no silent approval |
| M3.8 | Freeze accepted dataset release | M3.7, human decisions | Only current accepted items; count/coverage report; unresolved rows retained outside release; immutable release hash |
| M3.9 | Prepare blind judge-calibration packets | M3.8 | Human sees question/reference/evidence/answer but no model name, condition, or judge score until submitting |

## M4 — Human review facilities

| ID | Task / code target | Needs | Acceptance evidence |
|---|---|---|---|
| M4.0 | Static review workspace in `tools/evaluation/review/` | Existing dataset JSON | Delivered in this change: multi-file import, course filters, row detail, corrections, decisions, browser draft, JSON/CSV export |
| M4.1 | Export full evidence packets to workspace | M1.6 | Original document locator plus complete chunk context; preserve source hashes |
| M4.2 | Add structured evidence editing | M0.1, M4.1 | Multiple quotes/IDs/locators; invalid references detected before acceptance |
| M4.3 | Add controlled review bundle import/freeze integration | M3.7 | Review UI export becomes a proposal until backend validation accepts it |
| M4.4 | Add optional authenticated shared review API/UI | M3.6 | Existing Clerk/RBAC reused; access scoped; revision conflicts rejected; identity server-derived |
| M4.5 | Add second-reviewer adjudication workflow | M4.4 | Disagreement retained, final adjudicator recorded; no overwrite of first review |

M4.4-5 can be omitted for one local reviewer. Current prototype identity is self-entered and local history is editable; do not claim tamper-proof review. JSON export is the durable handoff, not CSV and not browser storage alone.

## M5 — Controlled answer collection and reranking

| ID | Task / code target | Needs | Acceptance evidence |
|---|---|---|---|
| M5.1 | Inject workflow dependencies/config in `answer_question.py` | M0.2, M2.1 | Existing product defaults unchanged; evaluation instances isolated without mutating global settings |
| M5.2 | Separate candidate depth, rerank depth, final k and token budget | M5.1 | Candidate depth > 5 test; final context bound enforced; truncations recorded |
| M5.3 | Snapshot original candidates, final ranking and exact prompt context | M5.2 | Can reconstruct model input; effective reranker success separate from enabled flag; failures visible |
| M5.4 | Replace reused-session collector with per-item sessions | M3.8, M5.1 | Question order does not inject previous answers; no notes in primary dataset |
| M5.5 | Implement no-retrieval collector mode | M5.4 | No vector/rerank calls; answer policy permits prior knowledge; irrelevant metrics N/A |
| M5.6 | Implement matched no-rerank/rerank modes | M5.3-4 | Same candidates and final context budget; raw and final answer saved, including any policy-added text |
| M5.7 | Implement reviewed-evidence mode | M5.4 | Uses only the designated gold evidence; labelled diagnostic |
| M5.8 | Record valid refusals and dependency failures distinctly | M5.4 | Empty context is not automatically collection error; database/provider failure not mislabelled good refusal |
| M5.9 | Parameterise run collector across courses/configurations | M0.5, selected M5 modes | Restart and partial completion work; credentials and course scopes isolated |

## M6 — Metric plugins (each separately removable)

All tasks use M0.4 and compatible frozen sample records. Q01 also needs evaluation embeddings; most LLM-based metrics need M2.3. Each plugin has a metric version, required fields, N/A policy, finite numeric validation, rubric/prompt hash, and retained rationale when available. Existing summary code must reject NaN/Inf and distinguish scoring failures from valid zeros.

| ID | Plugin | Additional prerequisite | Acceptance evidence |
|---|---|---|---|
| M6.1 | Faithfulness | Exact context snapshot | Supported/unsupported synthetic claims and live pilot judged sensibly |
| M6.2 | Context recall | Reviewed reference | Missing evidence reduces claim coverage |
| M6.3 | Context precision before/after ranking | Ordered contexts | Relevant-first vs relevant-last fixture behaves correctly |
| M6.4 | Answer relevancy | M2.4 | Question/answer only supported; embed usage recorded |
| M6.5 | Factual precision/recall/F1 | Reviewed reference | Partial answer and wrong-claim cases separated |
| M6.6 | ID Recall@k | Human relevance labels | Exact expected values, alternative evidence groups handled |
| M6.7 | Precision@k/hit rate | Human relevance labels | Empty retrieval and fewer-than-k conventions tested |
| M6.8 | MRR | Human relevance labels | First relevant rank and no-hit fixtures |
| M6.9 | nDCG@k | Graded relevance labels | Ideal order = 1; zero-IDCG N/A |
| M6.10 | Refusal rates | Reviewed unanswerable set, M5.8 | Correct and false refusals counted separately |

See `programme-scope.md` for exact definitions and formulas. Do not implement all metrics inside one mandatory scorer invocation.

## M7 — Citation evidence and judging

| ID | Task / code target | Needs | Acceptance evidence |
|---|---|---|---|
| M7.1 | Instrument actual answer markers and cited chunk IDs | M5.3 | Distinguish retrieved source list from explicitly attached citations; unresolvable mappings visible |
| M7.2 | Add stable source labels to prompt/evidence and preserve markers | M7.1 | Claim references resolve; product prompt change versioned and applied consistently to compared RAG conditions |
| M7.3 | Deterministic citation target validation | M7.1 | Missing/wrong-source/out-of-scope targets fail |
| M7.4 | Claim decomposition and citation association | M7.2 | Quoted spans map to claim IDs and citation IDs; ambiguous links flagged |
| M7.5 | Custom RAGAS citation correctness | M7.3-4, M2.3 | Judge sees attached passage only; unrelated retrieved support cannot rescue a bad link |
| M7.6 | Custom RAGAS citation sufficiency | M7.4, M2.3 | Joint support succeeds; partial/missing support fails; no-claim N/A |
| M7.7 | Required-answer cited coverage | M7.6, reviewed reference | Terse incomplete answer cannot receive complete coverage |
| M7.8 | Human citation calibration | M3.9, human labels | Agreement report and documented rubric changes before final freeze |

Do not retrofit claims onto every existing source card and call that observed citation behaviour. For historical outputs without links, link-level metrics remain unavailable. A changed citation prompt creates a new system version.

## M8 — Usage and latency telemetry

| ID | Task / code target | Needs | Acceptance evidence |
|---|---|---|---|
| M8.1 | Add optional call-result/usage sink in LLM adapters | M0.1 | String-returning API remains compatible; usage and actual model captured without secrets |
| M8.2 | Instrument embedding, reranker, OCR and judge calls | M8.1 | All paid roles separated; retry/fallback attempt IDs prevent double counting |
| M8.3 | Add per-run/sample trace correlation | M0.3 | Artifacts join with spans; IDs not used as unbounded metric labels |
| M8.4 | Measure internal workflow stage durations | M5.3, M8.3 | Monotonic timers; generation not accidentally included in retrieval-only time |
| M8.5 | Measure client/HTTP latency and ingestion readiness | M8.3 | Distinguishes network/app/provider and queue delays |
| M8.6 | Add true Gemini streaming if TTFT is selected | M2.1 | First token precedes completion; cancellation/errors/usage handled; existing buffered path labelled honestly |
| M8.7 | Record cache condition and control protocol | M5.1 | Fresh-session and deliberate warm-cache experiments distinguishable |

## M9 — Cost reports and spending controls

| ID | Task | Needs | Acceptance evidence |
|---|---|---|---|
| M9.1 | Versioned provider price registry | None | Prices have unit/date/source/currency; no invented current rates |
| M9.2 | Per-call cost calculator | M8.1, M9.1 | Cached/reasoning usage handled without double counting; unknown != zero |
| M9.3 | Ingestion cost report | M8.2, M9.2 | Per document/page/course and retries |
| M9.4 | Serving cost report | M9.2 | Per attempt/success/configuration; failures included |
| M9.5 | Evaluation cost report | M9.2 | Authoring, judging and evaluation embeddings separate from student-serving cost |
| M9.6 | Explicit scenario projections | Selected cost reports | Volumes/assumptions exposed; actual and projected costs separated |
| M9.7 | Enforce run call/token/spend caps | M0.7, M9.2 | Stops dispatch before known ceiling; reserve estimates for in-flight/unknown usage; journal retained |

## M10 — Performance and scalability experiments

| ID | Task / code target | Needs | Acceptance evidence |
|---|---|---|---|
| M10.1 | Expand k6 config and authenticated identity pool | Existing `tests/system/load`, test accounts | Separate users/notebooks, request rates and workload files; tokens never exported |
| M10.2 | Add stub-provider infrastructure scenario | M10.1 | Clearly labelled; repeatable DB/vector/worker load without external model billing |
| M10.3 | Add bounded real-provider chat scenario | M10.1, M9.7 | Request/latency/error/cost observations tied to run |
| M10.4 | Add ingestion-only load | M10.1 | Completion measured at indexed state, not upload acknowledgment |
| M10.5 | Add mixed workload | M10.3-4 | Interaction between chat and ingestion reported |
| M10.6 | Add stress profile | Chosen workload | Predeclared stop criteria and recovery evidence |
| M10.7 | Add spike profile | Chosen workload | Queue/error recovery observed |
| M10.8 | Add soak profile | Chosen workload | Resource growth and sustained degradation observed |
| M10.9 | Post-hoc quality sample under load | M6 plugins, M10.3 | RAGAS judge excluded from load traffic; random/stratified response sample retained |
| M10.10 | API resource/replica scaling matrix | M10.2 or 3; deployment support | Same workload and service targets; speedup/efficiency reported |
| M10.11 | Worker scaling matrix | M10.4 | Same corpus and capacity targets |
| M10.12 | Corpus-size matrix | M1.6, M6 retrieval plugins | Controlled corpus sizes; index build and query cost recorded |

No immediate promise to run 50 real-model users for a long period. Pilot resource and cost measurements determine the bounded schedule. k6 scaffolds exist but k6 itself is not bundled by the repository.

## M11 — Format and cross-course studies

| ID | Task | Needs | Acceptance evidence |
|---|---|---|---|
| M11.1 | Verify parser/format support and extraction provenance | M1.5 | Recognised vs accurately extracted vs unsupported distinguished |
| M11.2 | Text-PDF study | M11.1, selected quality plugins | Per-format failures and distributions |
| M11.3 | Scanned-PDF/OCR study | M11.1, human transcripts | CER/WER on subset; source/version and OCR API cost |
| M11.4 | Table/multi-column study | M11.1, human structure labels | Relationship and reading-order checks, not only string accuracy |
| M11.5 | DOCX/PPTX study | M11.1 | Paragraph/table/slide locators and downstream metrics |
| M11.6 | Equations/code/diagrams study | M11.1, human labels | Explicit limitations, no claim of visual understanding from filename support |
| M11.7 | Matched content across formats | M11.1 | Content controlled so format is not confounded with difficulty |
| M11.8 | Seventeen-course generalisation report | Selected completed course runs | Per-course counts, exclusions and macro/micro aggregates; no unmeasured courses included |

## M12 — Analysis and final evidence

| ID | Task | Needs | Acceptance evidence |
|---|---|---|---|
| M12.1 | Domain-specific results tables | Completed selected modules | Counts, N/A, failures and score distributions visible |
| M12.2 | Paired bootstrap comparisons | Matched question/config IDs | Pair identity validated; intervals on changes not only means |
| M12.3 | Repeated-generation subset | M5 runner | Repeat IDs distinct; not counted as independent questions |
| M12.4 | Repeated-judge subset | M6 registry | Fixed answers; judge variance separate from answer variance |
| M12.5 | Human/judge agreement | M3.9, human labels | Confusion tables or appropriate agreement statistics; disagreements auditable |
| M12.6 | Failure taxonomy and examples | Completed selected modules | Extraction/retrieval/reranking/generation/citation/operational causes separated |
| M12.7 | Reproduction bundle | M0.3, final runs | Dataset/config hashes, revision plus dirty-state evidence, packages, model identity, commands, hardware and prices |
| M12.8 | Final limitations and conclusion report | Selected reports | Claims restricted to measured courses/configurations/hardware; no fabricated student outcomes |

## Build sequence and release gates

1. **Now:** documents, review workspace, intake instructions. No paid model calls or production data changes.
2. **First build slice:** M0.1-4, M1.1-2, M3.5-6, M4.1-3. Goal: one trustworthy reviewed course packet and validated review import, using existing files if necessary.
3. **Provider/collector slice:** M2.1-6, M5.1-6, M8.1-4, M9.1-2/7. Goal: a small, bounded Gemini/Kimi/DeepSeek pilot with isolated sessions, reranking and costs.
4. **Quality slice:** chosen M6 and M7 tasks, M3.8-9. Goal: human-reviewed pilot and auditable metrics.
5. **Scale intake to 17:** M1.3-7, M3.1-4, per-course review batches. Freeze test releases only as humans complete them.
6. **Optional studies:** load/scalability/formats/repeats. Each has its own budget and artefacts.
7. **Analysis:** M12 and per-course report. Do not tune on held-out results and silently rerun them as an unchanged final benchmark.

Tests are targeted: schemas/review state transitions, provider fixtures, collector isolation, metric arithmetic and N/A, resume/idempotency, actual user review flows, and selected live smoke calls. Full production regression tests are required when shared app adapters/workflows change. Static documentation does not need mirrored tests.

## Immediate human inputs and decisions

- Course directory or 17 notebook mappings; authoritative files/exclusions.
- Reviewer identity and external reviewer availability.
- Pilot courses and subject-matter competence boundaries.
- Exact model IDs if preferred; otherwise select after probes. Embedding and reranker remain explicit open choices.
- Pilot API budget, full-run ceiling, deployment/hardware, and eventual latency/error objectives.

These inputs block their corresponding paid/data experiments, not offline facility construction. The first prototype deliberately does not accept API keys or call providers.

## Provider references

Verify concrete versions at implementation time: [OpenRouter API](https://openrouter.ai/docs/quickstart), [routing/fallbacks](https://openrouter.ai/docs/guides/routing/model-fallbacks), [Gemini generation/usage metadata](https://ai.google.dev/api/generate-content). Pricing/model availability is not frozen by this document.
