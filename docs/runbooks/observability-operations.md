# Runbook: Observability Operations

## Scope

This runbook covers the observability stack that now lives in the repo, how to run it locally, what it collects, what it does not collect yet, and how to operate it during normal changes and incidents.

For the architecture and file map, see [observability-stack.md](/c:/Users/User/Desktop/eduground/docs/architecture/observability-stack.md).

## Stack in This Repo

The local and development observability stack consists of:

- OpenTelemetry Collector
- Prometheus
- Loki
- Promtail
- Tempo
- Grafana
- PostgreSQL exporter
- Redis exporter

Core infra still comes from [`docker-compose.dev.yml`](/c:/Users/User/Desktop/eduground/infra/docker/docker-compose.dev.yml), and observability is layered on top through [`docker-compose.observability.yml`](/c:/Users/User/Desktop/eduground/infra/docker/docker-compose.observability.yml).

## Startup and Shutdown

Start the full dependency stack with observability:

```powershell
docker compose -f infra/docker/docker-compose.dev.yml -f infra/docker/docker-compose.observability.yml up -d
```

Or use the root `Makefile`:

```powershell
make up-observability
```

Stop it:

```powershell
make down-observability
```

Inspect logs and service state:

```powershell
make logs-observability
make ps-observability
```

## Local Endpoints

- Grafana: `http://localhost:3000`
- Prometheus: `http://localhost:9090`
- Loki: `http://localhost:3100`
- Tempo: `http://localhost:3200`
- OTEL Collector health: `http://localhost:13133`
- OTLP gRPC ingest: `localhost:4317`
- OTLP HTTP ingest: `localhost:4318`

Default Grafana credentials are controlled by:

- `GRAFANA_ADMIN_USER`
- `GRAFANA_ADMIN_PASSWORD`
- `GRAFANA_ROOT_URL`

## What the Stack Collects

### Metrics

Prometheus scrapes:

- Prometheus itself
- OTEL Collector metrics export
- Loki
- Tempo
- PostgreSQL exporter
- Redis exporter
- Qdrant `/metrics`

### Logs

Loki receives logs from:

- Promtail scraping Docker container stdout
- OTEL Collector OTLP log ingestion

### Traces

Tempo receives traces from the OTEL Collector through OTLP.

## Important Local Limitation

Promtail only sees Docker container logs. If the API, worker, evaluator, or web app are running directly on the host, their stdout logs will not appear in Loki through Promtail alone.

For full local parity you need one of these:

- run app processes in containers
- emit OTLP logs from the app processes to the collector

This is expected for the current local setup and should be accounted for during debugging.

Additional assumptions in the current overlay:

- Promtail expects Docker socket access at `/var/run/docker.sock`
- Qdrant metrics are scraped locally at `qdrant:6333/metrics` without extra auth handling

## Dashboard Inventory

Starter dashboard:

- [`eduground-observability.json`](/c:/Users/User/Desktop/eduground/infra/docker/grafana/dashboards/eduground-observability.json)
- [`eduground-product-operations.json`](/c:/Users/User/Desktop/eduground/infra/docker/grafana/dashboards/eduground-product-operations.json)

Current panels:

- core targets up
- target availability by job
- HTTP request rate
- HTTP latency p95
- ingestion jobs by status
- recent error logs

Recommended dashboard for day-to-day product debugging:

- `Eduground Product Operations`
  - API request rate
  - API 5xx rate
  - retrieval latency p95
  - auth failures by reason
  - provider failures
  - citation validation outcomes
  - ingestion jobs and indexed chunk volume
  - dependency health
  - operational error logs

Grafana datasources and dashboard provisioning live under:

- [`grafana/provisioning/datasources`](/c:/Users/User/Desktop/eduground/infra/docker/grafana/provisioning/datasources)
- [`grafana/provisioning/dashboards`](/c:/Users/User/Desktop/eduground/infra/docker/grafana/provisioning/dashboards)

## Alert Inventory

Alert rules live in [`alerts.yml`](/c:/Users/User/Desktop/eduground/infra/docker/prometheus/alerts.yml).

Current baseline alerts:

- `ObservabilityTargetDown`
- `EdugroundApiErrorRateHigh`
- `EdugroundRetrievalLatencyHigh`
- `EdugroundIngestionFailuresSpike`

Some rules depend on application metrics that must be emitted by the app services. Until those metrics exist, only infrastructure-target rules will be fully active.

## What to Instrument

### API

- request count, errors, and latency by route and status
- auth failures
- provider call latency and status
- retrieval latency
- citation validation pass and fail counts

### Worker

- ingestion jobs queued, running, completed, and failed
- stage duration by parse, OCR, chunk, embed, and index
- retry counts
- vector upsert counts

### Evaluator

- evaluation duration
- samples processed
- pass and fail counts by metric
- regression failures

### Required Correlation Fields

- `request_id`
- `trace_id`
- `span_id`
- `user_id`
- `institution_id`
- `notebook_id`
- `source_id`
- `source_version_id`
- `ingestion_job_id`
- `provider`
- `model`

## Daily Operating Procedure

- check Grafana service health first
- open `Eduground Product Operations` before drilling into raw Loki logs
- verify Prometheus targets are up
- check recent ingestion failures
- review provider error spikes
- confirm Qdrant and Postgres remain healthy

## Before Release

- confirm the stack starts cleanly from compose
- confirm dashboards provision automatically
- confirm Prometheus sees expected targets
- confirm logs and traces can be correlated with `request_id` and `trace_id`
- confirm alert rules load without Prometheus parse errors

## Incident Triage

1. Identify the failing surface: upload, ingestion, retrieval, tutor, notes, quizzes, analytics, or platform.
2. Pull the `request_id`, `job_id`, `trace_id`, or affected `source_id`.
3. Check Grafana dashboards for latency, error rate, and dependency health.
4. Inspect Prometheus targets to decide whether the issue is app-side or infra-side.
5. Inspect Loki logs for the same request or job identifiers.
6. Open the matching Tempo trace when available.
7. Follow the domain runbook:
   - [failed-ingestion.md](/c:/Users/User/Desktop/eduground/docs/runbooks/failed-ingestion.md)
   - [reindex-source-version.md](/c:/Users/User/Desktop/eduground/docs/runbooks/reindex-source-version.md)
   - [vector-metadata-drift.md](/c:/Users/User/Desktop/eduground/docs/runbooks/vector-metadata-drift.md)
   - [citation-failure-spike.md](/c:/Users/User/Desktop/eduground/docs/runbooks/citation-failure-spike.md)
   - [model-prompt-rollback.md](/c:/Users/User/Desktop/eduground/docs/runbooks/model-prompt-rollback.md)

## Management Guidance

- Treat dashboards and alerts as part of the feature, not as platform-only cleanup.
- Keep one owner per domain surface: API, worker, evaluator, or platform.
- Add metrics, logs, and spans in the same change set as new feature work whenever possible.
- Tune alert thresholds after observing real traffic rather than leaving placeholder values indefinitely.

## Exit Criteria for an Operationally Visible Feature

A feature is not operationally complete until:

- failures are logged with correlation fields
- latency and success metrics exist
- at least one critical-path trace exists
- Grafana shows the feature
- an alert exists for sustained failure or degradation
