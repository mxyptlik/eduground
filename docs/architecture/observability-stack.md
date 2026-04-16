# Observability Stack

## Purpose

This document defines the self-hosted observability stack that ships with the repo for local and development parity with the Railway deployment model.

The goal is to make the platform operable with the same core telemetry components in each environment:

- OpenTelemetry Collector for telemetry fan-out
- Prometheus for metrics and alert rule evaluation
- Loki for logs
- Tempo for traces
- Grafana for dashboards
- Promtail for Docker container log shipping
- PostgreSQL and Redis exporters for dependency metrics

## Repo Files

The local stack lives under [`infra/docker`](/c:/Users/User/Desktop/eduground/infra/docker):

- [`docker-compose.dev.yml`](/c:/Users/User/Desktop/eduground/infra/docker/docker-compose.dev.yml) for PostgreSQL, Redis, Qdrant, and MinIO
- [`docker-compose.observability.yml`](/c:/Users/User/Desktop/eduground/infra/docker/docker-compose.observability.yml) for OTEL, Prometheus, Loki, Tempo, Grafana, Promtail, and exporters
- [`otel-collector/config.yaml`](/c:/Users/User/Desktop/eduground/infra/docker/otel-collector/config.yaml) for OTLP ingestion and telemetry routing
- [`prometheus/prometheus.yml`](/c:/Users/User/Desktop/eduground/infra/docker/prometheus/prometheus.yml) for scrape configuration
- [`prometheus/alerts.yml`](/c:/Users/User/Desktop/eduground/infra/docker/prometheus/alerts.yml) for alert rules
- [`loki/config.yml`](/c:/Users/User/Desktop/eduground/infra/docker/loki/config.yml) for log storage
- [`promtail/config.yml`](/c:/Users/User/Desktop/eduground/infra/docker/promtail/config.yml) for Docker log collection
- [`tempo/config.yml`](/c:/Users/User/Desktop/eduground/infra/docker/tempo/config.yml) for trace storage and metrics generation
- [`grafana/provisioning/datasources/datasources.yml`](/c:/Users/User/Desktop/eduground/infra/docker/grafana/provisioning/datasources/datasources.yml) for datasources
- [`grafana/provisioning/dashboards/dashboards.yml`](/c:/Users/User/Desktop/eduground/infra/docker/grafana/provisioning/dashboards/dashboards.yml) for dashboard provisioning
- [`grafana/dashboards/eduground-observability.json`](/c:/Users/User/Desktop/eduground/infra/docker/grafana/dashboards/eduground-observability.json) for the starter dashboard
- [`grafana/dashboards/eduground-product-operations.json`](/c:/Users/User/Desktop/eduground/infra/docker/grafana/dashboards/eduground-product-operations.json) for the product-focused operator dashboard

## Topology

Telemetry is expected to flow like this:

1. `apps/api`, `apps/worker`, and `apps/evaluator` emit OTLP traces, metrics, and logs to the OpenTelemetry Collector.
2. The collector exports:
   - traces to Tempo
   - metrics to Prometheus through the collector's Prometheus endpoint
   - logs to Loki
3. Promtail collects Docker container stdout and ships it to Loki.
4. Prometheus scrapes infrastructure services, exporters, and the collector's Prometheus exporter.
5. Grafana reads Prometheus, Loki, and Tempo for dashboards and investigation.

## Local Startup

Start the core dependencies and observability overlay together:

```powershell
docker compose -f infra/docker/docker-compose.dev.yml -f infra/docker/docker-compose.observability.yml up -d
```

Stop the full stack:

```powershell
docker compose -f infra/docker/docker-compose.dev.yml -f infra/docker/docker-compose.observability.yml down
```

The root `Makefile` also exposes:

- `make up-observability`
- `make down-observability`
- `make logs-observability`
- `make ps-observability`

## Default Local Endpoints

- Grafana: `http://localhost:3000`
- Prometheus: `http://localhost:9090`
- Loki: `http://localhost:3100`
- Tempo: `http://localhost:3200`
- OTLP gRPC: `localhost:4317`
- OTLP HTTP: `localhost:4318`
- Qdrant: `http://localhost:6333`

## Railway Parity Model

The local overlay is designed to match the production topology, not necessarily the exact hosting product:

- app services emit OTLP rather than talking directly to each telemetry backend
- self-hosted Qdrant remains an external dependency in both local and Railway environments
- Grafana, Loki, Tempo, and Prometheus can be self-hosted together locally and split or replaced in higher environments

This keeps instrumentation consistent even if the final production backend mix changes.

## Current Coverage and Limits

- Promtail currently captures Docker container logs only.
- Promtail assumes the Docker socket is available to the container at `/var/run/docker.sock`.
- If `uvicorn`, `dramatiq`, or the web dev server run on the host instead of in Docker, those process logs will not appear in Loki through Promtail alone.
- Full local parity for host-run services requires OTLP log export from the app processes or containerized app execution.
- Some alert rules and dashboard panels expect application metrics that must be emitted by the app services before those panels become fully populated.
- Prometheus currently scrapes local Qdrant from `qdrant:6333/metrics`; higher environments may require private-network routing or auth-aware scrape configuration.

## Dashboards and Alerts

The starter dashboard focuses on:

- core target availability
- target availability by job
- request rate
- request latency p95
- ingestion job counts by status
- recent error logs

The product operations dashboard focuses on:

- API route traffic and route latency
- 5xx error rate
- auth failures by reason
- provider failures
- retrieval latency and workflow duration
- citation validation outcomes
- ingestion throughput and failures
- dependency health
- operator-friendly error logs

The alert rules currently cover:

- observability and infrastructure target downtime
- elevated API 5xx rate
- elevated retrieval latency p95
- ingestion failure spikes

Treat these as the baseline set. Add feature-specific panels and alerts alongside new capabilities instead of leaving observability as a later task.
