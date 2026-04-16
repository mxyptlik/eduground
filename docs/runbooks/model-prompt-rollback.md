# Runbook: Model or Prompt Rollback

## Use when

- A new prompt version regresses faithfulness or policy behavior.
- A model change raises latency or weakens citations.

## Steps

1. Identify the last known-good model profile or prompt version.
2. Pin the affected notebook or environment back to the prior version.
3. Re-run evaluation and compare the regression set.
4. Promote only after the new version passes quality gates.

