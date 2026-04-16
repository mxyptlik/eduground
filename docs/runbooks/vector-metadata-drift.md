# Runbook: Vector Metadata Drift

## Symptoms

- Retrieval returns chunks that do not match relational filters.
- Notebook or source scope looks correct in SQL but not in Qdrant.

## Checks

1. Compare chunk metadata in Postgres with vector payload metadata.
2. Check the upsert and delete workflows for idempotency.
3. Verify inactive or deleted sources are excluded from retrieval.

## Recovery

1. Pause new indexing for the affected notebook or source.
2. Rebuild the vector points from authoritative relational metadata.
3. Resume retrieval and verify filter correctness.

