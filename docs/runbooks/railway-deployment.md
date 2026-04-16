# Railway Deployment Runbook

This runbook describes a production-style Railway deployment for Eduground using Docker images.

## Service topology

Deploy these app services:

- `api` (FastAPI HTTP service)
- `web` (built Vite static app served on port `8080`)
- `worker` (Dramatiq consumers)
- `scheduler` (periodic maintenance enqueuer)

Backing services (recommended managed/external):

- PostgreSQL (Railway Postgres)
- Redis (Railway Redis)
- Qdrant (Qdrant Cloud or a dedicated Railway Qdrant service with persistent volume)
- S3-compatible object storage (Cloudflare R2 recommended)

## Files prepared for Railway

- `docker-compose.railway.yml`
- `apps/api/Dockerfile`
- `apps/worker/Dockerfile`
- `apps/web/Dockerfile`
- `apps/worker/app/jobs/scheduler.py`
- `.dockerignore`

## Deploy strategy on Railway

Railway does not run one docker-compose stack as a single app process. Create separate Railway services:

1. Create service `api` from repo root using `apps/api/Dockerfile`.
2. Create service `worker` from repo root using `apps/worker/Dockerfile`.
3. Create service `scheduler` from repo root using `apps/worker/Dockerfile` and override start command to:

   ```bash
   python -m app.jobs.scheduler
   ```

4. Create service `web` from repo root using `apps/web/Dockerfile`.

`docker-compose.railway.yml` is primarily a parity contract for environment and startup semantics.

## Required environment variables

Set these in Railway shared variables (or per service as appropriate):

- `CURRICULUM_TUTOR_APP_ENV=production`
- `CURRICULUM_TUTOR_DATABASE_URL` (Railway Postgres connection string)
- `CURRICULUM_TUTOR_DATABASE_POOL_SIZE` (default `20`)
- `CURRICULUM_TUTOR_DATABASE_MAX_OVERFLOW` (default `40`)
- `CURRICULUM_TUTOR_DATABASE_POOL_TIMEOUT_SECONDS` (default `30`)
- `CURRICULUM_TUTOR_DATABASE_POOL_RECYCLE_SECONDS` (default `1800`)
- `CURRICULUM_TUTOR_REDIS_URL` (Railway Redis connection string)
- `CURRICULUM_TUTOR_QDRANT_URL`
- `CURRICULUM_TUTOR_QDRANT_API_KEY` (if used)
- `CURRICULUM_TUTOR_QDRANT_COLLECTION_NAME=eduground_chunks`
- `CURRICULUM_TUTOR_S3_ENDPOINT`
- `CURRICULUM_TUTOR_S3_BUCKET`
- `CURRICULUM_TUTOR_S3_ACCESS_KEY`
- `CURRICULUM_TUTOR_S3_SECRET_KEY`
- `CURRICULUM_TUTOR_S3_REGION=auto`
- `CURRICULUM_TUTOR_S3_USE_SSL=true`
- `CURRICULUM_TUTOR_OBJECT_STORAGE_BACKEND=s3`
- `CURRICULUM_TUTOR_CORS_ORIGINS` (your production frontend origin)
- `CURRICULUM_TUTOR_TRUSTED_HOSTS` (api hostnames)
- `CURRICULUM_TUTOR_WEB_BASE_URL` (public web URL)
- `CURRICULUM_TUTOR_API_PUBLIC_BASE_URL` (public api URL)
- `CURRICULUM_TUTOR_CLERK_ENABLED=true`
- `CURRICULUM_TUTOR_CLERK_SECRET_KEY`
- `CURRICULUM_TUTOR_CLERK_PUBLISHABLE_KEY`
- `CURRICULUM_TUTOR_CLERK_JWKS_URL`
- `CURRICULUM_TUTOR_CLERK_JWT_ISSUER`
- `CURRICULUM_TUTOR_OPENROUTER_API_KEY`

Worker/scheduler scaling variables:

- `CURRICULUM_TUTOR_WORKER_PROCESSES` (default `2`)
- `CURRICULUM_TUTOR_WORKER_THREADS` (default `4`)
- `CURRICULUM_TUTOR_MAINTENANCE_INTERVAL_SECONDS` (default `3600`)
- `CURRICULUM_TUTOR_MAINTENANCE_STARTUP_DELAY_SECONDS` (default `30`)

Web build variables (for `apps/web/Dockerfile` build args):

- `VITE_API_BASE_URL`
- `VITE_CLERK_PUBLISHABLE_KEY`

## Ports

- API uses `$PORT` (Railway injects this).
- Web serves static files on `$PORT` with a default of `8080`.

## Cron and background jobs

There is no system cron dependency in the repository. Periodic work is handled by the `scheduler` service loop, which enqueues `cleanup_deleted_sources` into Redis-backed Dramatiq at a fixed interval.

## Notes

- Do not commit `.env`; use Railway variables.
- If you self-host Qdrant on Railway, attach persistent storage.
- Keep `CURRICULUM_TUTOR_AUTO_CREATE_SCHEMA=true` only if you accept startup schema creation. For stricter control, disable it and run migrations separately.
