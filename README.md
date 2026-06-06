# Eduground

Eduground is a greenfield curriculum tutoring platform built as a RAG-first monorepo.

## Current product state

This repository now includes a working monorepo for the curriculum tutor product:

- `apps/web` for the Vite + React client shell
- `apps/api` for the FastAPI backend, contracts, models, routes, and workflows
- `apps/worker` for ingestion, OCR heuristics, chunking, and indexing jobs
- `apps/evaluator` for offline golden-QA style evaluation
- `packages/prompts` for versioned tutor, quiz, citation, and policy prompts
- `infra/docker` for local Postgres, Redis, Qdrant, and MinIO infrastructure
- `docs` for ADRs, API contracts, and operational runbooks

## Chosen stack

- Web: Vite + React + TypeScript
- API: FastAPI + SQLAlchemy 2 + Alembic + Pydantic v2
- Jobs: Dramatiq + Redis
- Data: PostgreSQL, Qdrant, Redis, S3-compatible storage

## Runtime contract

- API config uses `CURRICULUM_TUTOR_*` env vars, with legacy aliases still accepted for local compatibility.
- The web app uses `VITE_API_BASE_URL` to point at the API server, defaulting to `http://localhost:8000`.
- Local bootstrap expects `PostgreSQL`, `Redis`, `Qdrant`, and `MinIO` from `infra/docker/docker-compose.dev.yml`.
- Automatic schema creation is controlled by `CURRICULUM_TUTOR_AUTO_CREATE_SCHEMA`.
- The product no longer depends on seeded demo users or demo notebooks. Create a real account through the Clerk-backed web app and select or create an organization before using notebook-scoped features.
- The primary online provider path is `OpenRouter` for chat and embeddings, with Qdrant for vector search and S3-compatible object storage for uploads.
- Object storage now supports resilient mode: `R2/MinIO` primary with a local directory fallback (`CURRICULUM_TUTOR_OBJECT_STORAGE_BACKEND=auto`, or `s3`/`r2` with local fallback enabled) so uploads/reads can continue when primary storage is unavailable.
- For local Clerk development, the webhook endpoint is optional. Session verification and read-repair sync can run without `CURRICULUM_TUTOR_CLERK_WEBHOOK_SIGNING_SECRET`; add it later when you want live Clerk webhook ingestion.

## Layout

- `apps/` application packages
- `packages/prompts/` versioned tutor, quiz, citation, guardrail, and policy prompts
- `infra/docker/` local dependency stack
- `docs/` ADRs, API contracts, and runbooks

## Documentation

- [Test architecture](/c:/Users/User/Desktop/eduground/docs/architecture/test-architecture.md)
- [Logging architecture](/c:/Users/User/Desktop/eduground/docs/architecture/logging-architecture.md)
- [Project structure](/c:/Users/User/Desktop/eduground/docs/architecture/project-structure.md)
- [Observability stack](/c:/Users/User/Desktop/eduground/docs/architecture/observability-stack.md)
- [Observability operations runbook](/c:/Users/User/Desktop/eduground/docs/runbooks/observability-operations.md)

## Quick start

1. Run `make bootstrap` to create `.env` from `.env.example` if needed.
2. Install Python and Node dependencies with `python -m pip install -r requirements-dev.txt` and `npm install`.
3. Start the local stack with `make up`.
4. Run the app services you need with `make api-dev`, `make worker-dev`, `make web-dev`, or `make evaluator-run`.
5. Open the web app, create an account, create a notebook, and upload a source.
6. Use `make down` to stop the local dependency stack.

## Git Bash Runbook (Copy/Paste Commands)

These commands are written for Git Bash on Windows and do not require `make`.

### 1. Bootstrap and install deps

```bash
cd /c/Users/User/Desktop/eduground
cp -n .env.example .env
python -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
npm install
```

### 2. Start infra only (Postgres, Redis, Qdrant, MinIO)

```bash
docker-compose -f infra/docker/docker-compose.dev.yml pull
docker-compose -f infra/docker/docker-compose.dev.yml up -d
docker-compose -f infra/docker/docker-compose.dev.yml ps
```

Optional: run app containers (`api`, `worker`, `web`, `evaluator`) with the `app` profile:

```bash
docker-compose -f infra/docker/docker-compose.dev.yml --profile app up -d
docker-compose -f infra/docker/docker-compose.dev.yml --profile app ps
```

### 3. Start observability stack (OTEL, Prometheus, Loki, Tempo, Grafana)

```bash
docker-compose -f infra/docker/docker-compose.dev.yml -f infra/docker/docker-compose.observability.yml pull
docker-compose -f infra/docker/docker-compose.dev.yml -f infra/docker/docker-compose.observability.yml up -d
docker-compose -f infra/docker/docker-compose.dev.yml -f infra/docker/docker-compose.observability.yml ps
```

### 4. Start app services locally (separate terminals)

API:

```bash
cd /c/Users/User/Desktop/eduground
source .venv/Scripts/activate
export PYTHONPATH=apps/api
python -m uvicorn app.main:app --reload --reload-dir apps/api --reload-exclude .venv --reload-exclude node_modules --host localhost --port 8000
```

Worker:

```bash
cd /c/Users/User/Desktop/eduground
source .venv/Scripts/activate
export CURRICULUM_TUTOR_WORKER_PROCESSES="${CURRICULUM_TUTOR_WORKER_PROCESSES:-2}"
export CURRICULUM_TUTOR_WORKER_THREADS="${CURRICULUM_TUTOR_WORKER_THREADS:-4}"
export PYTHONPATH=apps/worker
python -m dramatiq --processes "$CURRICULUM_TUTOR_WORKER_PROCESSES" --threads "$CURRICULUM_TUTOR_WORKER_THREADS" app.jobs.ingestion app.jobs.maintenance
```

Web:

```bash
cd /c/Users/User/Desktop/eduground
npm run --workspace @eduground/web dev -- --host localhost --port 5173 --strictPort
```

Evaluator:

```bash
cd /c/Users/User/Desktop/eduground
source .venv/Scripts/activate
export PYTHONPATH=apps/evaluator
python -m app.main
```

### 5. Run all tests

```bash
cd /c/Users/User/Desktop/eduground
source .venv/Scripts/activate
export PYTHONPATH=apps/api
python -m pytest apps/api/tests -q
export PYTHONPATH=apps/worker
python -m pytest apps/worker/tests -q
export PYTHONPATH=apps/evaluator
python -m pytest apps/evaluator/tests -q
npm run --workspace @eduground/web test:unit
npm run --workspace @eduground/web test:e2e
npm run prompts:validate
```

### 6. Optional system tests

```bash
cd /c/Users/User/Desktop/eduground
source .venv/Scripts/activate
python tests/system/observability/verify_traceability.py --api-base-url http://localhost:8000
```

```bash
npm run test:system:load:read
npm run test:system:load:chat
```

### 7. Shutdown

```bash
docker-compose -f infra/docker/docker-compose.dev.yml -f infra/docker/docker-compose.observability.yml down
```

## Test matrix

Use these commands to run the full local test suite:

- `make api-test`
- `make worker-test`
- `make evaluator-test`
- `make web-test-unit`
- `make web-test-e2e`
- `make test-all` (runs all commands above in sequence)

## Worker scaling

Worker parallelism is now fully env-driven. Update these values in `.env`:

- `CURRICULUM_TUTOR_WORKER_PROCESSES` (default `2`)
- `CURRICULUM_TUTOR_WORKER_THREADS` (default `4`)

These apply to local `make worker-dev` and to the optional Docker `worker` service command.

## Observability stack

Bring up the self-hosted observability overlay on top of the local dependency stack with:

```powershell
make up-observability
```

That starts OTEL Collector, Prometheus, Loki, Promtail, Tempo, Grafana, and the PostgreSQL and Redis exporters alongside the core local services.

Useful local endpoints:

- Grafana: `http://localhost:3000`
- Prometheus: `http://localhost:9090`
- Loki: `http://localhost:3100`
- Tempo: `http://localhost:3200`
- OTLP gRPC: `localhost:4317`
- OTLP HTTP: `localhost:4318`

Local storage fallback routes (used only when fallback is active):

- `PUT /api/storage/local-upload?token=...`
- `GET /api/storage/local-download?token=...`

Optional app containers are available in `infra/docker/docker-compose.dev.yml` under the `app` profile, with Dockerfiles in:

- `apps/api/Dockerfile`
- `apps/worker/Dockerfile`
- `apps/evaluator/Dockerfile`
- `apps/web/Dockerfile`

## Root commands

- `make bootstrap`
- `make up`
- `make up-observability`
- `make down`
- `make down-observability`
- `make logs`
- `make logs-observability`
- `make ps`
- `make ps-observability`
- `make api-dev`
- `make worker-dev`
- `make web-dev`
- `make web-test-unit`
- `make web-test-e2e`
- `make api-test`
- `make worker-test`
- `make evaluator-test`
- `make test-all`
- `make evaluator-run`
- `make check`
- `make test`
- `make lint`
- `make format`
- `make prompts-validate`

## System test scaffolding

- `npm run test:system:load:read` runs the k6 read-path scaffold in `tests/system/load/read_path.js`
- `npm run test:system:load:chat` runs the k6 tutor/chat scaffold in `tests/system/load/chat_path.js`
- `npm run test:system:observability -- --api-base-url http://localhost:8000` runs the observability verifier in `tests/system/observability/verify_traceability.py`

See `tests/system/README.md` for environment variables, artifact verification, and end-to-end usage.

## Known local environment blockers

- If `docker-compose` returns Docker daemon permission errors on Windows, run Git Bash as Administrator or ensure your user has Docker Desktop access to `//./pipe/docker_engine`.
- If `docker-compose pull` fails with TLS/proxy errors against Docker Hub, fix host Docker Desktop/proxy/TLS settings first.
- `k6` is not bundled in this repository; install it separately to run load scripts.

## Why MinIO Exists

`MinIO` is the local S3-compatible storage service used for development and Docker parity.

- In local Docker, API/worker can talk to MinIO exactly like they talk to R2 (`S3` API contract).
- In production, you point the same S3 settings at Cloudflare R2 (or another S3-compatible backend).
- With storage fallback enabled, the app can also spill to a local disk directory when the primary S3/R2 endpoint is unavailable.
