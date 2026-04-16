# System Test Scaffolding

This folder contains repo-level system-test scaffolding for load and observability verification without changing application code.

## Layout

- `load/`
  - k6-style load scenarios for API read paths and tutor chat paths
- `observability/`
  - traceability verification utilities for `request_id`, `job_id`, and `run_id`

## Current scopes

- request/response load on `/health` and authenticated notebook read paths
- chat-session creation plus grounded answer path load
- verification that HTTP responses echo `X-Request-Id`
- verification that exported log or artifact files include correlation identifiers

## Expected traceability contract

- HTTP responses should include `X-Request-Id`
- API logs and artifacts should include `request_id`
- worker logs and artifacts should include `job_id`
- evaluator logs and artifacts should include `run_id` or a configured equivalent field
- structured artifacts should also include `trace_id` when available

## How to run

### k6 read-path load

```bash
k6 run tests/system/load/read_path.js
```

Useful environment variables:

- `K6_API_BASE_URL`
- `K6_BEARER_TOKEN`
- `K6_ACTIVE_ORG_ID`
- `K6_NOTEBOOK_ID`
- `K6_VUS`
- `K6_DURATION`

### k6 tutor/chat load

```bash
k6 run tests/system/load/chat_path.js
```

Additional environment variables:

- `K6_CHAT_MESSAGE`

### Observability verification

```bash
python tests/system/observability/verify_traceability.py --api-base-url http://localhost:8000
```

Optional artifact verification:

```bash
python tests/system/observability/verify_traceability.py \
  --api-base-url http://localhost:8000 \
  --job-artifact path/to/worker-events.jsonl \
  --run-artifact path/to/evaluator-runs.jsonl
```

## Next extensions

- browser/E2E system tests for Clerk sign-in and organization onboarding
- direct verification against OTLP or log backend queries
- notebook write-path and ingestion throughput scenarios
