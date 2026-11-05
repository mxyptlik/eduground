# Eduground concurrency improvement plan

## Implementation update — 7 October 2026

Branch: `codex/concurrency-performance`. The API, worker, web and tooling changes below are in the working tree. This checkout already contained unrelated evaluation and corpus changes, which remain untouched. The original assessment and task definitions below remain the acceptance contract.

| Tasks | Implemented in branch | Still required for acceptance |
| --- | --- | --- |
| S01 | Retrieval timing separated from generation; first content and completion metrics; SQL connection hold histogram and bounded metric labels. | Instrument auth, SQL checkout wait, embedding/vector/rerank and cancellation distinctly in an actual service run. |
| S02 | Authenticated k6 read/chat/mixed profile, distinct identity requirement, five-minute read search and token-free run manifest. | Deterministic provider-stub deployment, per-process resource capture and realistic identities/data. |
| S03–S05 | Shared bounded HTTP/Redis clients, bulk retrieval hydration, short read transaction, atomic answer graph. | Verify pool behavior and database checkout duration against live PostgreSQL under load. |
| S06–S07 | Bounded chat history/context; current question deduplicated; Redis global/user/org/session generation leases with optional two-second wait and rejection metrics. | Simultaneous-turn version tests, provider spend reservation and overload measurement. |
| S08–S10 | Qdrant search avoids collection metadata GET; payload-index operator script; latest-job window query and serving indexes; bounded source pages; source revision polling with single-flight/backoff/visibility pause. | Apply/measure indexes on actual services, PostgreSQL query plans and browser regression under load. |
| S11–S13 | Optional killable parser subprocess, laptop worker limits, prepared batch reuse, bounded embedding/upsert, durable batch claim/ack and inspection tool. | Heavy OCR queue separation, per-document RSS/queue-age study, durable parsed-artifact cache, safe recovery after uncertain provider charges. |
| S14–S15 | Corpus-aware retrieval cache keys and a shared-storage replica configuration starting point. | Distributed single-flight, full replica deployment/load balancer, metrics aggregation and drain tests. |
| S16 | Offline test suites and migration dry run pass; capacity run scripts and reporting paths exist. | Real before/after read, chat, ingestion and mixed runs with authenticated identities, running services, quality samples and a soak. |

Validation in this checkout: 87 API tests, 97 worker tests, web typecheck, 13 web unit tests, capacity-tool tests and isolated SQLite migration. These confirm code contracts only. No new concurrent-user capacity number is established; services are down and authenticated test identities were not supplied. Do not treat the earlier `/health` peak as product capacity.

The same isolated `/health` laptop profile was rerun on 7 October; its highest short confirmed point was 237 VUs under much lower background CPU load. See `evaluation/test_runs/laptop-load-20261007T010553Z/findings.md`. This is a health-path measurement, not evidence of authenticated student or AI-chat capacity, and does not isolate an improvement caused by this branch.

Ten paired 243-VU repeats then showed 6/10 immediate starts versus 10/10 five-second ramps passing; all 16 HTTP failures were on the first request from affected VUs. Ten duration-matched 75-VU trials passed with median p95 83 ms and range 53–245 ms. See `evaluation/test_runs/health-repeat-20261007T011847Z/findings.md`. The startup failure is intermittent and reproducible, but the exact socket-level cause and branch-caused speedup remain unproven.

Assessment date: 6 October 2026. Status: proposed engineering work, not implemented by this assessment.

## Evidence and scope

Repository directory mapping covered API, web, worker, evaluator, prompts, infrastructure, evaluation tools, system tests and documentation. Detailed reading focused on request handling, authentication, retrieval, generation, persistence, caching, ingestion, polling, telemetry and deployment. This is not a claim to have read every source/test line, generated artifact, dependency or course document. No credentials were read, servers started or paid model calls made for this assessment.

The saved laptop experiment is `evaluation/test_runs/laptop-load-20261006T192205Z/findings.md`: 75 health-check VUs passed an initial probe and a separate confirmation; confirmation p95 was approximately 650 ms at 62.49 requests/sec. It used one API worker, same-laptop k6, and no authenticated product requests or model generation. System-wide CPU approached 100% and memory usage exceeded 90%. This establishes neither API-attributable resource use nor concurrent-student capacity. A health result cannot predict chat capacity.

Existing foundations should be retained: FastAPI, PostgreSQL, Redis/Dramatiq, Qdrant, object storage, Clerk authorization, embedding batching, retrieval/history caches, stage instrumentation and RAGAS evaluation. The changes below fit existing ownership boundaries.

## Findings

| Area | Repository evidence | Implication |
| --- | --- | --- |
| Measurement | `apps/api/app/workflows/answer_question.py`, `persist_answer` computes retrieval latency after generation | Retrieval metric includes later work and cannot reliably identify the slow stage |
| Database lifetime | Request-scoped Session; workflow retrieves ORM objects before network generation; repository commit/refresh methods | SQL transactions can retain connections while providers run; verify checkout duration, then separate read/generate/write phases |
| Retrieval SQL | `_retrieve_candidates` and `_hydrate_cached_candidates` perform per-candidate `db.get`; note context loads all eligible notebook notes before Python filtering | Query work grows with candidates and notebook contents |
| Persistence | `repositories/chat.py` commits separate message, trace, answer and citation operations, with per-item refreshes | Extra round trips and partial-answer persistence risk |
| Provider connections | LLM/embedding/Qdrant adapters use per-call request paths; streaming uses an HTTPX request context | Introduce explicit application-lifetime connection pools rather than assuming reuse across requests |
| Vector retrieval | Qdrant adapter calls `_ensure_collection` on each search | Extra collection metadata request on the read path |
| Chat memory | `services/chat_memory.py` replays full SQL history and stores full JSON history; runtime refreshes after an answer | Long sessions increase database work, Redis payload and model context cost |
| Authentication | Verifier is cached; identity writes occur only on changes/periodic last-login touch; `get_auth_context` still builds full membership summary | Preserve existing optimizations; defer full profile assembly to routes that need it |
| Source listing | `list_latest_jobs_for_sources` loads job history to select latest jobs in Python | Replace history-sized results with database latest-per-source selection |
| Browser polling | Sources page refreshes source list every five seconds while processing | Overlapping/hidden-tab polls can add avoidable request volume |
| Ingestion | Batch pre-warm uses threaded parsing; normal per-source writer parses again; all vectors/points materialized | Heavy documents can amplify CPU/RAM use; retain batching while removing duplicate preparation |
| Worker registration | Ingestion imports batching at boot | Batch actor is registered even when Docker command names only ingestion and maintenance; no missing-module fix is needed |
| Replica readiness | Local object-storage fallback; process-local metrics and authentication rate limiter | Multiple replicas need shared storage semantics, distributed admission control and correct metric aggregation |

## Atomic implementation tasks

Each row is a separately reviewable change. Dependencies are explicit; optional work can be removed without silently weakening another task's acceptance criteria.

| ID / order | Implementation and owning modules | Acceptance evidence | Dependencies |
| --- | --- | --- | --- |
| S01 / first | Fix retrieval-only timing; separately measure auth, SQL checkout/wait, embedding, vector search, rerank, queue wait, first token and complete response. API workflow/runtime/observability and existing M8 tools. | Delayed fake generator does not change retrieval time; true first-token timing differs from completion; streaming errors and cancellations recorded; no request IDs as metric labels. | None |
| S02 / first | Extend existing M10 k6/tooling with distinct identities/notebooks, workload mixes and explicit stub/live modes. Fail fast if authenticated scenario lacks credentials instead of falling back to health. | Saved run manifest identifies workload, identities count, actual endpoint coverage, seed/data size, arrival rate, cache condition, source revision and dirty state. Generator and service resource use measured separately. | None; uses S01 when available |
| S03 / early | Own reusable HTTP and Redis clients in API lifespan; explicit connection limits, connect/read/pool timeouts and shutdown. Inject adapters through current service constructors. | Connections reused across calls; no per-request pool creation; outage and shutdown tests show bounded waits and no leaks. Preserve adapter interfaces and provider identity. | None |
| S04 / early | Batch chunk/source hydration in `answer_question.py`; filter private notes in SQL and bound candidates before ranking. | SQL count remains bounded as candidate count increases; ranks, citations and tenant/private-note isolation preserved. | None |
| S05 / early | Separate authorization/evidence reads, external generation and final answer write into short database units of work. Persist answer/trace/citations atomically. | Slow fake provider does not retain a checked-out SQL connection; injected write failure leaves no partial completed answer; streaming cancellation has an explicit state. | None; coordinate with S04 |
| S06 / early | Add bounded chat-history retrieval and context-token budget, preserving original messages in SQL. Avoid full replay after every turn; include concurrency/version handling for simultaneous turns. | Long sessions keep bounded provider input and memory; no lost/duplicated turns; selected conversational regression questions remain answerable. | None |
| S07 / early | Add per-user/org/provider admission limits and bounded queues for expensive generation, including quiz generation. Use shared Redis state across API replicas, expiry and release-on-cancel. | Overload returns documented 429/503 with retry guidance; accepted work remains responsive; no permit leaks; rejection/queue rates reported separately from successes; paid attempts reserved before dispatch. | S03 useful, not required |
| S08 / next | Remove collection checks from every Qdrant query using initialization/short-lived validated metadata state and recovery. Audit payload indexes for actual filters; add only measured useful indexes. | Steady-state search avoids redundant metadata calls; collection replacement/missing collection recovers safely; retrieval relevance/scope unchanged. | None |
| S09 / next | Optimize latest-job SQL; paginate potentially large lists; inspect PostgreSQL execution plans and add targeted indexes. Return full auth membership summaries only where required. | Bounded response sizes and query counts; query plans measured on representative data; API/web pagination contract tested; Clerk and notebook authorization unchanged. | None |
| S10 / next | Add source-poll single-flight, visibility pause, jitter and failure backoff in the web client/page. Prefer compact changed-job status responses when justified. | At most one in-flight poll per view; hidden tabs pause; visible job completion still appears within documented delay; stale responses cannot overwrite current notebook. | Compact endpoint can depend on S09; client-only changes independent |
| S11 / next | Add laptop ingestion concurrency profile; isolate heavy parsing/OCR from interactive traffic with bounded worker queues and killable parsing subprocesses. | Large PDF timeout releases memory/process; queue age and per-document RSS recorded; browsing/chat SLOs remain satisfied during ingestion. | S01/S02 for measured mixed-load evidence |
| S12 / next | Reuse prepared parse/chunk artifacts between batch pre-warm and the writer, keyed by source hash plus parser/chunker/config versions. Bound embedding requests by tokens/bytes as well as count; bound vector upsert size. | One preparation per unchanged document; same chunk IDs/text/locators; correct vector-to-chunk mapping; restart-safe retries without duplicate billed work where provider outcome is known. Keep OCR/vision provenance and ledger integration. | None; coordinate with corpus-driver owner before shared worker edits |
| S13 / next | Make batch/job dispatch recoverable: claim/ack or durable pending state, idempotent writes, stale-job recovery, bounded retry of transient failures. Current destructive buffer pop and zero-retry actors require explicit recovery design. | Kill after claim, embedding and index write; jobs recover with consistent SQL/vector state; terminal failures remain visible; unknown provider charges are not silently retried as free. | Coordinate with S12, can ship independently |
| S14 / later | Harden cache lifecycle: shared clients, bounded TTLs, source-version/config keys, invalidation and scoped single-flight on repeated retrieval. | Source changes invalidate affected entries; permissions and private notes never cross users; cold/warm outcomes reported; concurrent misses do not duplicate identical expensive work unnecessarily. | S03 recommended |
| S15 / later | Replica-ready deployment profile: shared durable object storage, graceful draining, one migration runner, aggregate/per-replica metrics, pool budget and shared rate limits. Serve built web assets separately. | Requests/streaming survive normal drain; uploaded files accessible across replicas; no duplicate scheduler work; total SQL connections within configured database budget. | S07; shared-state audit before scaling |
| S16 / gate | Run controlled before/after read, chat, ingestion and mixed tests; repeat near the boundary and run a soak. Sample successful live answers for post-run RAGAS quality/citation checks. | Report sustained passing load, first failing range, throughput, p50/p95/p99, errors/rejections, queue age, recovery, CPU/RAM and cost. No capacity claim derived solely from health or stub responses. | S02; assess each completed change independently |

## Laptop starting configuration to benchmark

Start with one API process without reload and one heavy parser at a time. Compare one versus two API processes only after collecting per-process resource and pool-wait evidence. Set batch preprocessing concurrency explicitly so nested thread pools cannot silently undo the parser limit. Run the frontend as a built static app for deployment comparisons. Keep optional observability services and evaluation judging outside the serving benchmark unless they are part of the intended deployment.

These are starting experiments, not proven optimal settings. Increasing threads, workers and database pools together may increase memory pressure and contention on this four-logical-CPU, approximately 8-GB laptop.

Database connection budget must include every API process, worker and maintenance client:

`sum(process_count * (pool_size + max_overflow)) + other_clients + operational_reserve <= database_connection_limit`

The current API defaults allow up to 60 pooled/overflow connections per process; four such processes could allow 240 before worker/other connections. Configure from a global budget rather than copying defaults to every replica.

## Measurement contract

Measure four separate capacities: authenticated browsing, active answer generation/streams, ingestion jobs and mixed use. Registered accounts, idle browser sessions, VUs, requests/sec and in-flight model calls are different quantities.

Use the same data, request mix, response sizes, model configuration, cache conditions and hardware for comparisons. Preserve raw JSON/CSV, console logs, per-process resources, configuration, stage traces and failure details in unique run directories. Never export tokens or secrets. Maintain a short five-minute discovery option, but label it preliminary; use longer repeated confirmation/soak before promising sustained capacity.

Suggested initial targets to agree before testing: authenticated reads p95 below 1 second; unexpected failures below 1%; chat p95 first token below 5 seconds and completion below 30 seconds on a stated model/output budget. These are proposed product targets, not industry standards or current achievements. Report deliberate overload rejection separately; a system that rejects most traffic has not passed capacity merely because completed requests are fast. Include a queue-delay limit and ingestion completion requirement for the chosen corpus.

For engineering tests, inject deterministic provider doubles in an isolated test application while retaining SQL/vector operations and authorization checks. They establish infrastructure capacity only. Live-provider tests require genuine test identities, verified model configuration and available approved budget; do not disable production Clerk authentication. Human dataset review is not a prerequisite for infrastructure tuning, but reviewed questions enable meaningful live quality regression checks.

## Rollout and scope

Implement S01/S02, then S03–S07, followed by measured database/vector/browser changes and ingestion isolation. Re-measure after each meaningful change. Defer horizontal replication until a single-instance bottleneck and shared-state behavior are understood. Do not introduce Kubernetes, database sharding or a new backend framework as a first response.

This extends M8 (latency), M9 (cost/admission), M10 (capacity experiments) and M11 (document processing) in the existing implementation plan. It does not replace the corpus build, question authoring, human review or judging workflow. No runtime implementation was changed during this assessment.

References: [HTTPX client connection reuse](https://www.python-httpx.org/advanced/clients/), [k6 load-generator sizing and monitoring](https://grafana.com/docs/k6/latest/testing-guides/running-large-tests/).
