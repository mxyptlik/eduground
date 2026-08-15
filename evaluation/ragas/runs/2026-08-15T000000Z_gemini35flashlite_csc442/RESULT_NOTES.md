# Completed RAGAS Run Notes

## Result

- Framework: RAGAS 0.4.3
- Evaluator: Google Gemini `gemini-3.5-flash-lite`
- Samples: 100 collected and 100 scored
- Failures: 0
- Mean faithfulness: 0.9602
- Mean context recall: 0.9700

The raw per-sample results are in `ragas-scores.jsonl`; the computed aggregate is in `summary.json` and `summary.md`.

## Fixed system configuration

- System under test: local EduGround internal grounded-answer workflow
- Notebook: `a8fef64e-24a7-4d17-a032-3376589fbc93`
- Vector database: Qdrant collection `eduground_ragas_gemini_v1`
- Retrieval: dense, top-k 5, notebook/status metadata scope, reranker disabled
- Response and embedding provider chain: normal OpenRouter wrapper with Gemini fallback; OpenRouter was unavailable and the configured Gemini fallback was used

`retrieval-config.json`, `source-manifest.json`, `ragas-inputs.jsonl`, `scoring-config.json`, and `requirements.freeze.txt` retain the corresponding evidence.

## Dataset limits

The 100-item dataset was Gemini-authored from the frozen CSC442 sources and passed an automated source-integrity check. It was not independently human-reviewed. Eight of the eleven frozen documents contributed the 100 samples; the complete frozen corpus and source checksums remain in `source-manifest.json`.

This technical evaluation measures retrieval-grounded answer support and reference-answer coverage for this controlled course-material dataset. It does not measure learning gain, usability, pedagogical effectiveness, production reliability, or performance on unrelated users and documents.

## Runtime limit

The execution completed on Python 3.14, where LangChain emitted a Pydantic-v1 compatibility warning during RAGAS import. The score files are valid records of this run, but reproduce the same bundle on Python 3.12 before presenting the figures as final thesis evidence.
