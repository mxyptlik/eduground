# ADR 003: Qdrant Over pgvector

## Status

Accepted

## Context

The tutor depends on filtered retrieval by notebook, course, module, source, and policy scope.

## Decision

Use Qdrant as the primary vector store.

## Consequences

- Strong metadata filtering for scoped retrieval.
- Cleaner separation between relational metadata and vector search.
- PostgreSQL remains the source of truth for business data.

