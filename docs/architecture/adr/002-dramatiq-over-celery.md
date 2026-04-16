# ADR 002: Dramatiq Over Celery

## Status

Accepted

## Context

The job runner needs a lean, Redis-backed worker stack for ingestion and background work.

## Decision

Use Dramatiq for `apps/worker`.

## Consequences

- Smaller operational surface area for MVP.
- Clear queue and actor model for ingestion jobs.
- Durable orchestration engines can be introduced later if needed.

