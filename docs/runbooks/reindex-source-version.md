# Runbook: Reindex Source Version

## Use when

- A source file was replaced or normalized content changed.
- Vector data is out of sync with the latest source version.

## Steps

1. Mark the old source version as inactive if needed.
2. Create a new source version record.
3. Re-run parse, chunk, embed, and index jobs.
4. Confirm retrieval traces point at the new chunk set.

