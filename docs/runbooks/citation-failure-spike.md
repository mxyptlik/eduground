# Runbook: Citation Failure Spike

## Symptoms

- Answers are being refused more often than expected.
- Citation coverage falls below threshold across notebooks.

## Checks

1. Review recent prompt or model changes.
2. Inspect retrieval traces for low-recall evidence packs.
3. Verify citation validation rules have not become too strict.

## Recovery

1. Roll back the prompt or model version if the spike is tied to a deployment.
2. Re-run golden QA evaluation.
3. Restore the last known-good prompt version if needed.

