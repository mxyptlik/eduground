# RAGAS Run Bundle

Create one folder per real execution, for example `runs/2026-08-14T153000Z_gemini_local_v1/`. Do not overwrite a completed run.

Required contents:

1. `manifest.json`: run ID, UTC timestamps, operating-system details, and non-secret environment values.
2. `dataset.json`: immutable copy of the approved 100-item dataset.
3. `dataset.sha256`: SHA-256 of `dataset.json`.
4. `source-manifest.json`: source IDs, titles, checksums, notebook ID, page/slide locators, and Qdrant collection configuration snapshot.
5. `retrieval-config.json`: copy of the exact retrieval configuration.
6. `evaluator-config.json`: collector-time evaluator configuration.
7. `scoring-config.json`: exact RAGAS version, Gemini adapter/model, metrics, input hash, execution pacing, retry policy, and relevant package versions used for scoring.
8. `ragas-inputs.jsonl`: one row per live EduGround response containing `user_input`, `response`, `retrieved_contexts`, `reference`, citation metadata, and provider/model metadata.
9. `ragas-scores.jsonl`: one row per metric result, including failures and retry count.
10. `summary.json` and `summary.md`: aggregate metrics, sample count, exclusions, and failure counts.
11. `runtime-provenance.json` and `command.txt`: code revision, Docker container snapshot, and the collection command. Capture redacted stdout/stderr logs when available; the structured JSONL and summary artifacts are the required raw scoring outputs.
12. `requirements.freeze.txt`: `pip freeze` from the evaluator container or virtual environment.

Never include API keys, `.env`, session cookies, bearer tokens, or signed object-storage URLs.
