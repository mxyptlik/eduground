# M8–M11 terminal runner

Run this from **Git Bash** in the Eduground checkout. It writes live terminal output and a separate log for each check in a new timestamped directory under `evaluation/test_runs/`.

```bash
cd /c/Users/User/Desktop/eduground
bash tools/evaluation/run_m8_m11_tests.sh
```

If the API is already running on another port, pass its URL. The runner never starts a server.

```bash
bash tools/evaluation/run_m8_m11_tests.sh --api-url http://127.0.0.1:8001
```

Add a bounded 30-second k6 health-read workload if k6 is installed and the API is running:

```bash
bash tools/evaluation/run_m8_m11_tests.sh --with-k6
```

Once a collected run and frozen reviewed release exist, include the existing report command. Repeat `--condition` for each condition present in that run. This renders the run's M8 latency and M9 cost report inside `<run>/artifacts/report/`; the runner saves the command's output in its own log.

```bash
bash tools/evaluation/run_m8_m11_tests.sh \
  --run /c/Users/User/Desktop/eduground/evaluation/runs/YOUR_RUN_ID \
  --release /c/Users/User/Desktop/eduground/evaluation/releases/YOUR_RELEASE \
  --condition no_retrieval \
  --condition no_rerank \
  --condition rerank \
  --condition reviewed_evidence
```

Use the actual run/release paths and condition IDs from that run; the paths above are placeholders. To use a different course directory, add `--courses /absolute/path`. A different Python executable can be selected with `EDUGROUND_PYTHON=/absolute/path/to/python.exe`.

The runner executes the M8 telemetry and M9 pricing/ledger test files in separate evaluation and worker pytest processes. It executes M11 format tests, inventories the current course directory, and attempts one bounded, read-only PDF, PPTX and DOCX extraction per available type. M10 uses 20 `/health` requests at each of 1, 5 and 10 concurrent clients when the API responds. The optional k6 run also reads `/health` only. Neither sends a model API request or performs OCR.

`summary.tsv` records `PASS`, `FAIL`, and `SKIP`; `logs/*.log` contains the same output shown in the terminal; `health_load.json`, `formats.json`, and optional `k6_read_health.json` contain machine-readable observations. The script continues after a failed check and exits nonzero if any executed check fails. A `SKIP` is a missing prerequisite, **not** evidence that the corresponding M8–M11 implementation-plan task is complete.

In particular, a health-read check cannot establish multi-user chat scalability. The runner deliberately marks authenticated multi-user, provider-backed chat load, streaming TTFT, real invoice reconciliation, extraction accuracy against human transcripts, and 17-course generalisation as pending. These require prepared identities, bounded shared-ledger live runs, a frozen reviewed dataset, reference labels, and/or declared workload and cost assumptions. Suffix recognition and one extraction sample do not establish correct reading order, tables, equations, or diagrams.

## Five-minute search across 8,000–14,000 virtual users

Start the API without `--reload` and leave its database/dependencies running. Then, from Git Bash:

```bash
cd /c/Users/User/Desktop/eduground
source .venv/Scripts/activate
python tools/evaluation/find_health_peak.py --api-url http://127.0.0.1:8000
```

The script makes at most eight 25-second k6 probes within a **300-second total wall-time budget**, first checking 8,000 and 14,000 VUs, then bisecting a pass/fail bracket. It reuses the read-only `/health` script with authentication explicitly cleared. Its predeclared pass rule is p95 below 1,500 ms, failed-request rate below 5%, check rate above 99%, all requested VUs active, and at least 0.5 requests per VU-second. It does not loosen the existing k6 thresholds. Logs, raw k6 summary JSON per probe, `probes.csv`, and `report.json` are saved under a new `evaluation/test_runs/health-peak-*` directory and printed as probes finish.

The pure summary/threshold calculations have offline contract tests, which the main M8–M11 runner now includes. To run only those tests from your terminal: `python -m pytest evaluation/program/tests/test_terminal_m10_m11_tools.py -q`.

`highest_passing_tested_vus` is the reported result. If 14,000 passes, the upper capacity is unknown. If 8,000 fails, this search has no passing point in the requested range. A missing k6 summary, generator timeout, or missed VU target is **inconclusive**, not an Eduground capacity result. The test uses one local k6 process and one lightweight endpoint, so it is not a measure of distinct students, authenticated notebook traffic, or provider-backed chat. Large VU counts can saturate the load generator, local networking, or the development API; inspect the per-probe logs before attributing failure to the server. Twenty-five-second unreplicated probes give a quick bracket, not a production capacity guarantee.

## Every document in the 17-course corpus

The following command attempts each supported course file in its **own process**, using the worker's parse, normalize, and chunk functions. It does not upload, OCR, embed, or index:

```bash
cd /c/Users/User/Desktop/eduground
source .venv/Scripts/activate
python tools/evaluation/profile_course_documents.py --courses Courses
```

The output directory `evaluation/test_runs/course-profile-*` contains `inventory.json` (course/file hashes), `run_config.json`, a copy of the dated price registry when live modes are enabled, `documents.jsonl` (flushed after each document), `documents.csv` (one row per file), `courses.csv` (per-course totals, means, medians, and p95s), `calls.jsonl` (one row per guarded provider attempt), and `summary.json` (overall totals, modes, nonsecret model configuration, ledger state, registry hash, and worker code hashes). Timings include read, parse, optional OCR, optional visual enrichment, normalize, chunk, optional embed, in-child stage total, and full process wall time. Each statistic includes its sample count; failed and timed-out files remain explicit. The default per-document timeout is 300 seconds. Scanned PDFs are marked as needing OCR, but the default offline run does not OCR them. Parsing and generated figure descriptions are **not** human extraction-accuracy labels. This all-corpus run is not constrained to five minutes; that limit applies only to the k6 search.

Direct embeddings are optional and require neither Eduground HTTP nor Clerk. The production single-file worker already sends a list of all chunks from one file in one embedding request. The profiler now packs each **individual file** into bounded batches (default 32 chunks and 100,000 UTF-8 text bytes per request), using the same `embed_texts` function, shared pilot ledger, and fixed $10 cap:

```bash
python tools/evaluation/profile_course_documents.py --courses Courses \
  --embed \
  --ledger evaluation/pilot/budget.sqlite3 \
  --prices evaluation/prices/2026-09-30.openrouter.json
```

Adjust with `--embed-batch-size 16 --embed-max-batch-bytes 50000` if the provider rejects larger payloads. The per-file output contains batch sizes, elapsed times, call IDs, vector dimensions, request counts, settled USD, and unknown attempts. No vector is saved or indexed. Batching reduces HTTP calls and overhead; token-based charges still depend chiefly on total input tokens. A different batch size can change provider limits and latency, so keep the settings in the report.

OCR and figure enrichment are separately opt-in. OCR follows the worker trigger rules (PDF parse failure, parser scan flag, empty extracted text, or high scanned-page ratio with visual mode). Vision uses the configured primary and fallback providers/models and its existing cache. The visual summary records figures found, described, omitted, deduplicated, unsupported, chart-derived, and errors. `calls.jsonl` records provider/model, usage counts where returned, provider-reported cost where returned, reserved and settled USD, status, bound, and elapsed time for each guarded attempt; `summary.json` aggregates each role's known cost. An unknown attempt may still be billed and stops later live calls.

The image-card fallback remains separate. For page text, set `CURRICULUM_TUTOR_OCR_VISION_ENABLED=true` to enable the dedicated OpenRouter fallback using the pinned `deepseek/deepseek-v4.1-flash` model. It renders PDF pages (or accepts PNG/JPEG files), transcribes each complete page, and records per-page render time, image bytes, transcription time and text length. The fallback runs after Unstructured fails or is unconfigured; it is not used for image-card descriptions. Unsupported formats cannot use the page-text fallback. If a guarded Unstructured request may already have been billed and its charge is unknown, the shared $10 ledger blocks the DeepSeek call until that attempt is reconciled. This is a budget safeguard, so not every Unstructured network failure can immediately trigger a paid fallback.

The checked-in OpenRouter registry has embedding and one image-card model price, the Gemini registry has its image-card model price, and `2026-09-30.deepseek-ocr.json` supplies the sourced DeepSeek page-OCR bound. None includes Unstructured OCR pricing. Build a combined registry from a **verified current Unstructured OCR rate** if you will use that provider (the example numbers below are placeholders; replace them with your sourced rate):

```bash
python tools/evaluation/prepare_profile_prices.py \
  --output evaluation/prices/pilot-profile.json \
  --ocr-unit pages --ocr-amount-usd YOUR_RATE --ocr-quantity 1 \
  --ocr-source 'YOUR_DATED_PRICE_URL_OR_CONTRACT'
```

If OCR is charged per request, use `--ocr-unit requests` and the correct quantity. The script does not guess prices. To profile all enabled stages, pin `CURRICULUM_TUTOR_VISUAL_PROVIDER`, `CURRICULUM_TUTOR_VISUAL_MODEL`, and optionally `CURRICULUM_TUTOR_VISUAL_FALLBACK_PROVIDER` plus `CURRICULUM_TUTOR_VISUAL_FALLBACK_MODEL` in `.env`, and provide the relevant OpenRouter/Gemini and Unstructured keys. Then run:

To use DeepSeek page OCR without Unstructured, omit the `--ocr-*` price arguments when preparing the registry, leave the Unstructured key unset, and enable `CURRICULUM_TUTOR_OCR_VISION_ENABLED=true` in `.env`. The pinned model is `deepseek/deepseek-v4.1-flash`; `CURRICULUM_TUTOR_OCR_VISION_MAX_PAGES` and `CURRICULUM_TUTOR_OCR_VISION_MAX_OUTPUT_TOKENS` are optional limits. Check the [current OpenRouter model page](https://openrouter.ai/deepseek/deepseek-v4.1-flash) before a paid run because prices and provider routes can change.

```bash
python tools/evaluation/profile_course_documents.py --courses Courses \
  --ocr --vision --embed \
  --ledger evaluation/pilot/budget.sqlite3 \
  --prices evaluation/prices/pilot-profile.json
```

The same ledger path must be used across corpus building, authoring, collection, judging, and this profiler if their spending belongs to one $10 pilot. If an existing shared ledger has a different cap, the profiler's $10 preflight rejects the live run; reconcile the cap before spending, rather than creating a second ledger. Avoid running two live corpus jobs against the same files: the profiler would make extra provider calls, separate from the SQLite/Qdrant corpus driver. The profiler is for measurement, while the corpus driver builds the actual evaluation index. Preflight refuses missing keys, unpriced pinned models, incomplete fallback settings, or an unavailable cap before dispatch. After a live-stage failure, the script continues offline parsing/chunking later files and marks live stages skipped. Review the dated price registry against your provider account before a live run.

The local corpus builder also accepts `--ocr` and `--vision`. With `CURRICULUM_TUTOR_OCR_VISION_ENABLED=true` and `CURRICULUM_TUTOR_OPENROUTER_API_KEY` in `.env`, its `--ocr` path uses Unstructured first when configured, then DeepSeek V4.1 Flash for page text when safe under the shared ledger. For a DeepSeek-only pilot without an Unstructured rate/key, prepare the combined registry without `--ocr-*` rate arguments and leave `CURRICULUM_TUTOR_UNSTRUCTURED_API_KEY` unset. For the full build, after the other agent's free smoke has been reviewed and Qdrant is running:

```bash
python tools/evaluation/prepare_profile_prices.py \
  --output evaluation/prices/pilot-profile.json

python tools/evaluation/build_local_corpus.py --courses Courses \
  --ocr --embed --index \
  --db evaluation/local_corpus/corpus.sqlite3 \
  --ledger evaluation/pilot/budget.sqlite3 \
  --prices evaluation/prices/pilot-profile.json
```

Add `--vision` only after pinning and pricing the separate image-card primary and fallback models. A new manifest directory is chosen by default; do not resume an earlier manifest built without OCR/vision because its existing chunks would not be regenerated. The corpus manifest records OCR provider/page events, figure summary, timings, per-file known provider cost, and the final shared-ledger totals. A failed or unknown-billed Unstructured request may block DeepSeek fallback and later paid calls; inspect the manifest and ledger before retrying.

## Clerk from the terminal (development instance)

Eduground's protected HTTP routes require a Clerk session bearer token. The repo has no password-login API. With a configured **Clerk development** instance and an existing test user, use:

```bash
python tools/evaluation/clerk_terminal_token.py --list-users
python tools/evaluation/clerk_terminal_token.py --user-id user_YOUR_TEST_USER_ID
```

The helper uses `CURRICULUM_TUTOR_CLERK_SECRET_KEY` from `.env`, requests a 600-second test token, saves it in a create-only `.jwt` file under `evaluation/test_runs/clerk-tokens/`, and validates it against `/api/auth/me`. It never prints the key or token. If the API uses port 8001, add `--api-url http://127.0.0.1:8001`. A development secret (`sk_test_`), enabled Clerk auth, valid JWKS/issuer settings, and a synced test user are required. Clerk's backend session-creation endpoint is intended for testing and is unavailable to production instances.

For a bounded authenticated **read-only** check, use the token file path printed by the helper and set a real organization and notebook ID:

```bash
TOKEN_FILE=/c/Users/User/Desktop/eduground/evaluation/test_runs/clerk-tokens/YOUR_SESSION_ID.jwt
mkdir -p output/k6
K6_API_BASE_URL=http://127.0.0.1:8000 \
K6_BEARER_TOKEN="$(cat "$TOKEN_FILE")" \
K6_ACTIVE_ORG_ID=org_YOUR_ORG_ID K6_NOTEBOOK_ID=YOUR_NOTEBOOK_ID \
K6_VUS=5 K6_DURATION=30s \
k6 run --summary-export output/k6/auth-read.json tests/system/load/read_path.js
```

Do not print or commit the token file. It expires; create a fresh token before a longer run. One bearer token shared across 5 or 8,000 VUs remains **one identity**. A real many-user study needs separate test users, memberships, notebooks, and a token-refresh/identity-pool protocol. The current `chat_path.js` sends paid model requests and should only be used with the shared spend ledger and a separately bounded workload.
