# Logging Architecture

## Purpose

This document defines how Eduground should emit logs, what fields must be present, what data must never be logged, and how logs should be routed and operated.

## Goals

- Make failures diagnosable without reading raw databases.
- Correlate web actions, API requests, worker jobs, and evaluator runs.
- Keep sensitive learning content and credentials out of logs.
- Support Railway-hosted services plus self-managed dependencies such as Qdrant.

## Logging Model

All application services should emit structured JSON logs to stdout.

Services in scope:
- `apps/api`
- `apps/worker`
- `apps/evaluator`
- web client telemetry routed to the API or directly to an observability backend

## Transport and Collection

The repo's local and development logging path is:

1. container stdout logs are collected by Promtail
2. OTLP logs can be sent to the OpenTelemetry Collector
3. Loki stores log streams for search and correlation
4. Grafana is the primary operator interface for log exploration

Relevant files:

- [`docker-compose.observability.yml`](/c:/Users/User/Desktop/eduground/infra/docker/docker-compose.observability.yml)
- [`otel-collector/config.yaml`](/c:/Users/User/Desktop/eduground/infra/docker/otel-collector/config.yaml)
- [`promtail/config.yml`](/c:/Users/User/Desktop/eduground/infra/docker/promtail/config.yml)
- [`loki/config.yml`](/c:/Users/User/Desktop/eduground/infra/docker/loki/config.yml)

Important local limitation:

- Promtail only captures Docker container logs.
- Host-run `uvicorn`, worker, and frontend dev-server logs will not appear in Loki unless those processes emit OTLP logs or are containerized.

## Required Base Fields

Every log line should include:

- `timestamp`
- `level`
- `service`
- `environment`
- `version`
- `message`
- `request_id` or `job_id`
- `trace_id`
- `span_id`

## Domain Correlation Fields

Include when available:

- `user_id`
- `institution_id`
- `notebook_id`
- `source_id`
- `source_version_id`
- `ingestion_job_id`
- `chat_session_id`
- `chat_message_id`
- `quiz_id`
- `quiz_attempt_id`
- `evaluation_id`

## Provider and Runtime Fields

For external calls include:

- `provider`
- `model`
- `operation`
- `latency_ms`
- `status_code`
- `retry_count`
- `timeout`

For ingestion and retrieval include:

- `stage`
- `chunk_count`
- `segment_count`
- `indexed_count`
- `retrieval_top_k`
- `citation_count`

## Event Categories

### Security and Auth

- sign-in and sign-out
- failed auth attempts
- role or scope denials
- suspicious request rejection

### Upload and Ingestion

- upload intent created
- upload verified
- source version created
- ingestion stage entered and completed
- parser, OCR, embedding, and indexing failures

### Retrieval and Tutor

- query received
- retrieval finished
- citation validation passed or failed
- provider call started and finished
- refusal or insufficient-evidence outcome

### Notes and Quizzes

- note created, updated, exported, converted
- quiz generated
- attempt started and submitted
- scoring completed

### Admin and Operations

- audit log exports
- evaluation runs
- migration runs
- maintenance or repair jobs

## Sensitive Data Rules

Never log:

- passwords
- tokens
- API keys
- cookies
- raw authorization headers
- full uploaded document bodies
- full note content
- full quiz answers when not strictly required
- full prompt bodies unless explicitly sampled in a secure debugging environment

Allowed alternatives:

- IDs instead of content
- counts instead of raw payloads
- hashes or fingerprints for dedup and correlation
- truncated excerpts only when strictly necessary and approved

## Log Levels

- `DEBUG`: local troubleshooting only
- `INFO`: normal state changes and stage completions
- `WARNING`: retries, degraded behavior, partial failures
- `ERROR`: user-facing or operator-facing failures
- `CRITICAL`: data loss risk, security risk, unrecoverable outage

## Service-Specific Guidance

### API

- Log one summary line per request.
- Log one summary line per provider call.
- Log authorization denials with request path and scope context.
- Log source finalize and reindex outcomes with source and version identifiers.

### Worker

- Log stage transitions, retries, and terminal states per ingestion job.
- Log parser and embedding failures with the current stage and source version.
- Log vector write counts and cleanup actions.

### Evaluator

- Log dataset name, sample count, model profile, and result summary.
- Log threshold failures distinctly from infrastructure failures.

### Web

- Capture frontend route errors, upload failures, and API failure summaries.
- Do not emit raw user content by default.

## Routing and Storage

Recommended flow:

1. Application logs to stdout in JSON.
2. Runtime or sidecar collector ships logs to the log backend.
3. OTLP-capable services send logs through the OpenTelemetry Collector.
4. Docker container logs are shipped by Promtail.
5. Logs are indexed by `service`, `environment`, `request_id`, and `trace_id`.

Recommended backends:

- Grafana Loki for cost-sensitive deployments
- Elastic or OpenSearch where richer full-text search is required
- DataDog or similar managed platforms when operational budget supports it

## Retention

Recommended defaults:

- `INFO` and above: 30 to 90 days
- `ERROR` and `CRITICAL`: 90 to 180 days
- frontend client telemetry: shorter retention unless required for incident analysis

Retention should match institutional privacy requirements and deletion policy.

## Operational Queries That Must Be Easy

- show all failures for one `request_id`
- show all stages for one `ingestion_job_id`
- show all provider failures by model and status code
- show all citation validation failures in the last 24 hours
- show all auth denials by route and role
- show all reindex operations for one `source_id`

## Implementation Guidance

- Use a shared logging helper or formatter per service.
- Emit machine-readable fields first; avoid free-form strings as the only source of meaning.
- Keep field names stable across services.
- Ensure request IDs are created at the API boundary and propagated to workers and downstream calls.
- Ensure trace IDs align with tracing instrumentation so logs and traces can be joined.
- Prefer OTLP log export for app services in higher environments so the same collector path is used locally and on Railway.
