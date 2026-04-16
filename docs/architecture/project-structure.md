# Eduground Project Structure

This document is the system map for the monorepo. It explains where each capability lives, how runtime components connect, and which folders should be changed for each class of feature.

## 1. Monorepo Topology

```text
eduground/
  apps/
    api/          FastAPI backend (contracts, auth, workflows, data access)
    web/          Vite + React frontend (Clerk UX, notebook/tutor/notes/quizzes UI)
    worker/       Dramatiq background processing (ingestion, chunking, indexing)
    evaluator/    Offline quality harness (golden QA, regression scoring)
  packages/
    prompts/      Versioned prompt templates and validation scripts
  infra/
    docker/       Local stack and observability stack compose/config files
  docs/
    architecture/ Architecture specs and ADRs
    api/          HTTP/data contracts
    runbooks/     Operational procedures and failure-response guides
  tests/
    system/       Cross-service load and traceability scaffolding
```

## 2. Application Boundaries

### `apps/api` (control plane and request path)
- `app/api/routes/`: HTTP surface and endpoint wiring.
- `app/api/deps.py`: Clerk auth context and active-organization enforcement.
- `app/core/`: settings, runtime validation, middleware, logging, error handling, observability, readiness.
- `app/services/`: domain services (notebooks, sources, chat, notes, quizzes, auth sync, analytics, audit).
- `app/workflows/`: multi-step orchestration (answer generation, source ingestion, quiz generation).
- `app/integrations/`: provider adapters (Clerk, OpenRouter, Qdrant, R2/S3, OCR).
- `app/models/`: SQLAlchemy domain models.
- `app/repositories/`: persistence access and data-shaping boundaries.
- `tests/`: unit/module/integration/system API coverage.

### `apps/web` (user experience plane)
- `src/features/`: feature slices (`auth`, `notebooks`, `sources`, `tutor-chat`, `notes`, `quizzes`, `analytics`).
- `src/lib/session/`: Clerk-backed app session and notebook selection state.
- `src/lib/api/`: typed API client and standardized error rendering.
- `tests/module/`: Vitest module-level tests.
- `tests/e2e/`: Playwright browser journeys (Clerk-aware and env-gated).

### `apps/worker` (ingestion/indexing plane)
- `app/jobs/`: Dramatiq entrypoints (`ingestion`, `maintenance`).
- `app/pipelines/`: staged ingestion pipeline (parse -> OCR fallback -> normalize -> chunk -> embed -> index).
- `app/services/`: parser/chunker/indexing/vector helpers.
- `app/telemetry.py`: worker spans/metrics/log context with `job_id` and stage labels.
- `tests/unit`, `tests/functional`: deterministic worker coverage.

### `apps/evaluator` (quality gate plane)
- `app/runners/`: golden QA runner orchestration.
- `app/scorers/`: scoring primitives.
- `app/telemetry.py`: run-level spans/metrics/log context with `run_id`.
- `tests/unit`, `tests/functional`: evaluator coverage.

### `packages/prompts` (prompt contract plane)
- Prompt families by purpose: tutor, quiz, citation, guardrail, and policy overlays.
- `registry.json` is the prompt-version index.
- `scripts/validate.mjs` validates structure and registration consistency.

## 3. Runtime and Data Flow

## 3.1 User request flow
1. Browser signs in via Clerk and gets a session token.
2. Web client calls API with `Authorization: Bearer <token>` and `X-Active-Organization-Id`.
3. API validates Clerk token, synchronizes identity/org membership, enforces notebook ACL.
4. API executes service/workflow, emits audit logs, and returns standardized API responses.

## 3.2 Source ingestion flow
1. Web requests signed upload URL.
2. File uploads to R2/S3-compatible storage.
3. API finalizes source and creates ingestion job/source version.
4. Worker parses/chunks/embeds/indexes into Qdrant.
5. API retrieval path consumes indexed chunks for grounded tutoring and citations.

## 3.3 Observability flow
1. Services emit structured JSON logs with request/job/run correlation IDs.
2. OTEL traces/metrics are emitted (when enabled) to OTLP collector.
3. Collector exports to Prometheus/Tempo/Loki backends.
4. Grafana dashboards and alerts provide operator visibility.
5. This same data plane is intended to back the future super admin/control board.

## 4. Infra and Container Layout

### `infra/docker/docker-compose.dev.yml`
- Core data and optional app services (`api`, `worker`, `web`, `evaluator`) via `app` profile.
- Worker concurrency is tunable by:
  - `CURRICULUM_TUTOR_WORKER_PROCESSES`
  - `CURRICULUM_TUTOR_WORKER_THREADS`

### `infra/docker/docker-compose.observability.yml`
- OTEL Collector, Prometheus, Loki, Promtail, Tempo, Grafana, and exporters.
- Merged with dev compose for full local stack.

### App Dockerfiles
- `apps/api/Dockerfile`
- `apps/worker/Dockerfile`
- `apps/evaluator/Dockerfile`
- `apps/web/Dockerfile`

These provide consistent container entrypoints for local parity and deployment packaging.

## 5. Testing Layout and Execution

### Backend
- `apps/api/tests`: unit/module/integration/system.
- `apps/worker/tests`: unit + functional.
- `apps/evaluator/tests`: unit + functional.

### Frontend
- Vitest: `apps/web/tests/module`.
- Playwright: `apps/web/tests/e2e`.

### System
- `tests/system/load`: k6 load scaffolding.
- `tests/system/observability`: correlation checks (`request_id`, `job_id`, `run_id`, optional `trace_id`).

## 6. Ownership Guide

- API/auth/contracts/change in provider interfaces: `apps/api`.
- User-facing behavior and interaction flows: `apps/web`.
- Ingestion/indexing throughput and reliability: `apps/worker`.
- Quality gate and regression rules: `apps/evaluator`.
- Prompt behavior changes: `packages/prompts`.
- Operational tooling and deployment runtime: `infra/docker` + `docs/runbooks`.

This split should be preserved to avoid coupling UI, provider adapters, and pipeline internals in a single change set.
