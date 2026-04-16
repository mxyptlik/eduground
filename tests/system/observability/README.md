# Observability Verification

This folder contains system-level observability verification utilities.

## Contract covered

- HTTP responses echo or generate `X-Request-Id`
- structured worker artifacts include `job_id`
- structured evaluator artifacts include `run_id`
- structured artifacts include `trace_id` when expected

## Primary verifier

```bash
python tests/system/observability/verify_traceability.py --api-base-url http://localhost:8000
```

## Artifact verification examples

```bash
python tests/system/observability/verify_traceability.py \
  --api-base-url http://localhost:8000 \
  --job-artifact logs/worker.jsonl \
  --run-artifact logs/evaluator.jsonl \
  --require-trace-id
```

## Accepted artifact formats

- JSON object
- JSON array
- JSON Lines (`.jsonl`)

The verifier treats each JSON object as a record and checks required identifier fields.
