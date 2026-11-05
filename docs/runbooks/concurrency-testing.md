# Concurrency testing

The initial 75-VU `/health` observation in `evaluation/test_runs/laptop-load-20261006T192205Z/findings.md` measured one local API worker and same-laptop k6. It is not a student/chat capacity rating.

Use `tests/system/load/capacity.js` to measure authenticated reads separately from model-backed chat. Create a local JSON file containing at least one distinct test identity per VU, each with `bearer_token`, `active_org_id` and `notebook_id`. Keep it outside tracked folders; never save tokens with results. Clerk terminal token tooling already exists in `tools/evaluation/clerk_terminal_token.py`.

Git Bash read-path example with an existing running API:

```bash
cd /c/Users/User/Desktop/eduground
mkdir -p evaluation/test_runs/capacity-manual
K6_API_BASE_URL=http://127.0.0.1:8000 \
K6_IDENTITIES_FILE=/c/secure/test-identities.json \
K6_WORKLOAD=read PRODUCT_VUS=5 PRODUCT_DURATION=2m \
K6_SUMMARY_FILE=evaluation/test_runs/capacity-manual/read-5.json \
k6 run tests/system/load/capacity.js 2>&1 | tee evaluation/test_runs/capacity-manual/read-5.log
```

The script fails if identities are missing. It does not fall back to `/health`. Chat mode requires `K6_ALLOW_LIVE_MODEL_CALLS=yes`; verify a separate approved provider spend limit before setting it. Each chat iteration creates a new session, so the workload measures uncached first turns. For an offline infrastructure run, use a test-only provider stub explicitly and label `K6_PROVIDER_MODE=stub`; never present stub results as live-model capacity.

For chat or mixed mode, also set `K6_MAX_MODEL_CALLS` to a positive integer. The script uses a bounded number of shared iterations, so a failed session can only reduce the actual answer count; it cannot exceed the specified count of answer attempts. Keep the provider ledger's spend cap independent of this request-count limit. Use `PRODUCT_VUS` and `PRODUCT_DURATION`; k6's built-in `K6_VUS`/`K6_DURATION` variables can override a script's bounded scenario and are rejected by this profile.

A bounded five-minute read-only search can run from Git Bash when the API is up:

```bash
PYTHONPATH=. python tools/performance/search_capacity.py \
  --identities /c/secure/test-identities.json --min-vus 5 --max-vus 100 \
  --budget-seconds 300
```

It streams k6 output to the terminal and saves each probe, confirmation, and `report.json` under a new `evaluation/test_runs/product-read-*` directory. A passing probe without a passing confirmation is not reported as a capacity result. The search needs an identity file with at least as many users as `--max-vus`.

Measure per-process API, worker, k6, PostgreSQL and Qdrant CPU/RAM, request p50/p95/p99, throughput, 429/503/5xx rates, SQL pool usage, provider first-content and completion times, worker queue wait and ingestion completion. Run generator and service on separate hosts for larger tests; a saturated generator invalidates a capacity claim. Repeat the largest passing load and use a longer soak before claiming sustained capacity.

`infra/docker/docker-compose.laptop.yml` is a constrained starting configuration. The replica overlay disables local object-storage fallback; it does not itself deploy a load balancer or solve multi-replica metric collection. Apply one database migration job before starting replicas. Pool sizes must be budgeted across *all* processes.

Generation admission uses shared Redis leases. The laptop overlay allows a request to wait up to two seconds for a slot; `CURRICULUM_TUTOR_GENERATION_ADMISSION_WAIT_SECONDS=0` rejects immediately. Track accepted, rejected and wait latency separately. The read-only capacity search writes a `manifest.json` with workload, host, Git state, k6 version and identity count but never tokens.

`GET /api/notebooks/{id}/sources` now returns at most 100 rows by default, with `limit` (maximum 200) and `offset` parameters. The web client walks pages when it needs the complete source list. Polling checks `/sources/revision` first and reloads pages only when the revision changes.

Batch ingestion now moves queued references into a claimed Redis list before dispatch and acknowledges each completed per-source actor. If the batch process stops, the claimed references stay visible. Run `python tools/performance/inspect_ingestion_claims.py --json` before deciding whether to reindex an interrupted source. Claims with uncertain provider spend are deliberately not replayed automatically.

## Repeated health-boundary experiment

The 7 October repeat evidence is in `evaluation/test_runs/health-repeat-20261007T011847Z/findings.md`. To repeat the paired experiment from Git Bash without Docker or credentials:

```bash
cd /c/Users/User/Desktop/eduground
source .venv/Scripts/activate
python tools/performance/repeat_health_load.py --repeats 10 --vus 243 --plateau-seconds 20 --ramp-seconds 5
```

The runner starts one isolated temporary API, alternates ten immediate starts and ten ramps, samples system CPU/RAM, saves every k6 summary/log, checks API health after every trial, and stops the API. Use `--modes constant --vus 75 --plateau-seconds 35` for ten trials matched to the original 75-VU confirmation. First-request connection refusals are counted separately from later failures. This remains a `/health` experiment.

## Testing actual product capacity

These are separate measurements. Do not infer any of them from `/health` VUs.

| Path | Required setup | Measurement |
| --- | --- | --- |
| Authenticated browsing | Running PostgreSQL, Redis, Qdrant, object storage and API; seeded notebooks/sources; one valid Clerk test token, org membership and notebook per VU. | `capacity.js` read mode or `search_capacity.py`; source/notes/notebook p50/p95/p99, throughput, 429/5xx, API and k6 resources. Repeat at the boundary and soak. |
| Retrieval infrastructure | Same real data/services plus a **test-only deterministic generation adapter**; retain real SQL/Qdrant and specify whether embedding is real or a controlled stub. This adapter is still outstanding. | Chat request volume and the API's retrieval-stage metrics, query counts, pool wait and Qdrant latency; inspect cold/warm cache and citation/source scope separately. Do not label stub answers as live AI capacity. |
| Live AI answers | Distinct identities, reviewed questions, pinned model settings, an approved provider budget and shared ledger. | Bounded `capacity.js` chat mode for complete-answer latency/errors/cost; a separate streaming client is needed for true first-content timing. Sample answer/citation quality after the run. |

`tools/evaluation/clerk_terminal_token.py` can create short-lived **development** Clerk tokens from the terminal for existing test users; it does not create users, memberships or a multi-identity k6 file. Keep the identity JSON outside the repository and verify token expiry exceeds the run. `capacity.js` rejects missing identities and requires one distinct identity per VU. Its chat/mixed scenario also requires `K6_MAX_MODEL_CALLS` and explicit live-model opt-in; an offline k6 integration test confirms three configured iterations make at most three answer calls. The prior $10 pilot budget is exhausted, so do not start a live-model load until a new spend cap is approved.

To establish that a code change caused a speedup, alternate the base revision and this branch on the same hardware with the same seeded corpus, provider mode, cache state, identities, think time and output size. Capture API, k6 and other process CPU/RAM separately and repeat each point; the 6 October versus 7 October health observations differ too much in background load to serve as an A/B test.
