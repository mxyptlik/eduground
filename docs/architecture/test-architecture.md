# Test Architecture

## Purpose

This document defines the recommended test layout, responsibilities, and execution model for Eduground. The goal is to keep tests close to the code they protect, reserve expensive end-to-end coverage for critical product flows, and make failures easy to localize.

## Principles

- Prefer fast deterministic tests first.
- Keep tests aligned to service boundaries: API, worker, evaluator, and web.
- Use real infrastructure for integration and system tests whenever behavior depends on PostgreSQL, Redis, Qdrant, or object storage.
- Keep provider-facing tests explicit. Unit tests should not silently stand in for OpenRouter, Clerk, or storage behavior.
- Every production incident should map to at least one regression test at the correct layer.

## Test Layers

### Unit Tests

Scope:
- Pure functions
- Schema validation
- Parsing, chunking, and scoring logic
- Policy and authorization decisions
- Small repository or service helpers with in-memory setup

Placement:
- `apps/api/tests/unit/`
- `apps/worker/tests/unit/`
- `apps/evaluator/tests/unit/`
- `apps/web/src/**/*.test.ts(x)` when frontend unit tests are added

Examples:
- request validator rejects unsafe filenames
- citation coverage calculation
- chunk overlap preservation
- prompt assembly
- quiz scoring rules

### Module Tests

Scope:
- A single module plus its immediate collaborators
- API services with a disposable database
- Worker service pipelines with real serialized inputs and outputs
- Web feature modules with router and API client boundaries controlled

Placement:
- `apps/api/tests/module/`
- `apps/worker/tests/module/`
- `apps/web/tests/module/`

Use module tests when unit tests are too narrow but full integration would be slow or noisy.

### Integration Tests

Scope:
- Component interactions across process or storage boundaries
- FastAPI route to service to database
- upload intent to storage verification to source persistence
- worker pipeline to embeddings/vector store
- evaluator runner to scoring output

Placement:
- `apps/api/tests/integration/`
- `apps/worker/tests/integration/`
- `apps/evaluator/tests/integration/`
- `apps/web/tests/integration/` for browserless UI-data integration if added

Required integration environments:
- PostgreSQL
- Redis
- Qdrant
- S3-compatible object storage

Integration tests should use the same containerized dependency profile used in local development.

### System Tests

Scope:
- Full product workflows across web, API, worker, storage, vector store, and background processing
- Account bootstrap, notebook creation, upload, ingestion, tutoring, notes, quizzes, analytics
- Failure and recovery flows such as provider outage or vector drift recovery

Placement:
- `tests/system/`
- `apps/web/tests/e2e/` for Playwright-driven browser journeys

System tests are the right place for:
- signup or sign-in through the real auth flow
- upload and indexing completion
- grounded answer generation with citations
- reindex, failure recovery, and administrative visibility

## Recommended Directory Layout

```text
apps/
  api/
    tests/
      unit/
      module/
      integration/
  worker/
    tests/
      unit/
      module/
      integration/
  evaluator/
    tests/
      unit/
      integration/
  web/
    src/
      **/*.test.tsx
    tests/
      module/
      e2e/
tests/
  system/
fixtures/
  documents/
  notebooks/
  evaluation/
```

## What Belongs Where

### API

- Unit:
  - validators
  - policies
  - small service decisions
- Module:
  - auth mapping
  - notebook membership and role enforcement
  - upload finalize orchestration
- Integration:
  - route contract behavior
  - DB persistence
  - audit log creation
  - storage and vector interactions

### Worker

- Unit:
  - parsing rules
  - OCR heuristics
  - chunk construction
- Module:
  - stage transitions
  - batch embedding behavior
  - vector payload assembly
- Integration:
  - real download, parse, chunk, embed, and index runs

### Evaluator

- Unit:
  - scoring math
  - threshold decisions
- Integration:
  - dataset loading
  - evaluation result persistence
  - regression comparison outputs

### Web

- Unit:
  - UI components
  - hooks and formatting helpers
- Module:
  - feature routes with session context
  - source upload progress behavior
  - quiz attempt state handling
- E2E:
  - notebook creation
  - source upload
  - tutor chat with citations
  - note conversion
  - quiz submission

## Required Coverage by Capability

- Auth and session:
  - success
  - unauthorized
  - expired session
  - role mismatch
- Upload and ingestion:
  - valid upload
  - duplicate checksum
  - storage mismatch
  - parser failure
  - embedding failure
  - reindex success
- Retrieval and tutoring:
  - scoped retrieval
  - insufficient evidence
  - citation persistence
  - provider failure surfaces clearly
- Notes and quizzes:
  - CRUD
  - conversion to source
  - grounded quiz generation
  - scoring and mastery updates
- Admin and analytics:
  - authorization
  - audit access
  - evaluation summaries

## Data and Fixture Strategy

- Store reusable sample documents under `fixtures/documents/`.
- Keep fixtures small, named, and provenance-aware.
- Use golden evaluation samples under `fixtures/evaluation/`.
- Avoid burying long inline payloads inside test files.
- Keep provider credentials out of tests; integration tests should read them from env only when explicitly required.

## CI Execution Model

Recommended gating:

1. Unit and module tests on every push.
2. API and worker integration tests on every pull request.
3. System and browser tests on pull requests for user-facing or ingestion changes.
4. Evaluator regression suite before release or model/prompt change promotion.

Recommended commands:

- `pytest apps/api/tests/unit apps/api/tests/module`
- `pytest apps/worker/tests/unit apps/worker/tests/module`
- `pytest apps/evaluator/tests/unit`
- `pytest apps/api/tests/integration apps/worker/tests/integration`
- `playwright test` from `apps/web/tests/e2e/` once added
- `pytest tests/system`

## Ownership and Incident Response

- API owners add or update API tests for route, schema, and persistence regressions.
- Worker owners add or update pipeline tests for ingestion and indexing regressions.
- Web owners add or update module or end-to-end tests for user-visible regressions.
- Evaluator owners add or update golden datasets and regression thresholds for model or prompt changes.

When a production issue is fixed:

1. Add the smallest failing test at the lowest useful layer.
2. Add a broader integration or system test if the issue crossed component boundaries.
3. Link the incident or bug ID in the test name or nearby comment if the context is not obvious.
