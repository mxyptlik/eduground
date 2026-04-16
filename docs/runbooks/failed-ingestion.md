# Runbook: Failed Ingestion

## Symptoms

- Source stays in `processing` or `failed`.
- OCR, parsing, or chunking jobs stop retrying.

## Checks

1. Inspect the ingestion job record.
2. Verify the raw file still exists in object storage.
3. Check worker logs for parser, OCR, or embedding errors.
4. Confirm the source checksum matches the uploaded object.

## Recovery

1. Fix the underlying parser or file issue.
2. Requeue the source ingestion job.
3. Confirm the source returns to `indexed`.

