# EduGround Real-RAGAS Evaluation Record

This directory is the reproducibility record for real RAGAS evaluations of EduGround. It is intentionally separate from `apps/evaluator/app/datasets`, whose existing `ragas_like.py` scorer is a lexical-overlap proxy and must not be reported as RAGAS.

## Completed evidence run

`runs/2026-08-15T000000Z_gemini35flashlite_csc442/` contains a completed local execution: 100 collected responses, 100 RAGAS-scored samples, and zero evaluator failures. Its aggregate results are recorded only in that run's `summary.json` and `summary.md`.

The evaluation dataset was Gemini-authored with a source-bounded prompt and passed an automated source-integrity gate. It was not independently human-reviewed. It therefore supports a controlled technical evaluation, not a claim that the questions or reference answers were manually curated. Eight of the eleven frozen CSC442 source documents contributed usable question material; the remaining three did not contribute samples because their indexed content was title-page or worksheet-instruction material under the authoring rules.

The completed execution used Python 3.14, which emitted a LangChain Pydantic-v1 compatibility warning during RAGAS import. Its package versions and raw outputs are retained, but a final thesis-quality reproduction should also be run under Python 3.12 as documented below.

## Intended system under test

The evaluation will exercise the local Docker deployment of EduGround: API, worker, PostgreSQL, Redis, Qdrant, and MinIO. Test questions will be sent through the real grounded-answer workflow after the selected notebook sources have completed ingestion.

For the Gemini-only run configuration, set the OpenRouter key blank or unavailable, set `CURRICULUM_TUTOR_GEMINI_FALLBACK_ENABLED=true`, configure a valid `CURRICULUM_TUTOR_GEMINI_API_KEY`, and set `CURRICULUM_TUTOR_RERANKER_ENABLED=false`. The current application uses Gemini as the fallback chat and embedding provider; it has no Gemini reranker implementation. A valid Gemini key with available quota is required.

## Before a run

1. Start Docker Desktop and run the local stack from `infra/docker/docker-compose.dev.yml`.
2. Configure a fresh Qdrant collection name for this run and re-ingest the complete frozen source-document set with Gemini embeddings. Do not mix vectors produced by a previous embedding provider in the same collection, even when the vector dimensions match.
3. Create a new evaluation notebook and upload the frozen source-document set.
4. Wait until every selected source is indexed, then record its title, checksum, source ID, and notebook ID in the dataset manifest.
5. Review and approve all 100 questions and reference answers. The older seed set is only a candidate: it contains programmatically generated items and at least some vague or malformed questions, so it must not be used unchanged as a final RAGAS dataset.
6. Create a 100-item source-bounded draft with `tools/evaluation/author_ragas_dataset.py`, then have an independent reviewer check each question, reference answer, supporting quote, locator, and `source_chunk_id`. Mark only accepted items as `review_status: "approved"`. A final collector refuses all other states and verifies that every approved reference context still appears in its recorded source chunk. The automatic approval helper records source-integrity checking only; it must not be described as human review.
7. Capture each live response, its retrieved context chunks, citation metadata, and provider/model metadata in the RAGAS input format described by `datasets/eduground_ragas_dataset.schema.json`.
8. Pin the installed RAGAS and dependency versions after the first successful run. `requirements.txt` records the packages installed locally on 2026-08-14; each run additionally retains `pip freeze`.

## Commands after the fresh evaluation notebook is indexed

Use Python 3.12 for the final scoring environment. The current local Python 3.14 environment imports RAGAS 0.4.3 through `ragas_compat.py`, but LangChain emits a Pydantic-v1 compatibility warning on Python 3.14. That warning should not be present in the final reproducibility environment.

Before creating the evaluation notebook, set these non-secret local settings in `.env`, then restart the API and worker so both ingestion and retrieval use the same controlled configuration:

```dotenv
CURRICULUM_TUTOR_OPENROUTER_API_KEY=
CURRICULUM_TUTOR_GEMINI_FALLBACK_ENABLED=true
CURRICULUM_TUTOR_RERANKER_ENABLED=false
CURRICULUM_TUTOR_QDRANT_COLLECTION_NAME=eduground_ragas_gemini_v1
```

`eduground_ragas_gemini_v1` is an example new collection name. Do not point the run at the existing `eduground_chunks` collection. Upload the frozen corpus after this change, not before it.

```powershell
$env:PYTHONPATH = "apps/api"
.\.venv\Scripts\python.exe tools\evaluation\author_ragas_dataset.py `
  --notebook-id <fresh-evaluation-notebook-id> `
  --output evaluation\ragas\datasets\eduground_100q.v1.assisted.draft.json

# Review the draft, mark all accepted rows approved, then validate it.
.\.venv\Scripts\python.exe tools\evaluation\validate_ragas_dataset.py `
  evaluation\ragas\datasets\eduground_100q.v1.approved.json --require-approved

.\.venv\Scripts\python.exe tools\evaluation\collect_ragas_inputs.py `
  --dataset evaluation\ragas\datasets\eduground_100q.v1.approved.json `
  --notebook-id <fresh-evaluation-notebook-id> `
  --run-dir evaluation\ragas\runs\<timestamp>_gemini_local_v1

.\.venv\Scripts\python.exe tools\evaluation\run_real_ragas.py `
  --inputs evaluation\ragas\runs\<timestamp>_gemini_local_v1\ragas-inputs.jsonl `
  --run-dir evaluation\ragas\runs\<timestamp>_gemini_local_v1
```

For the final Gemini-only evidence run, leave the OpenRouter key unavailable and retain `CURRICULUM_TUTOR_GEMINI_FALLBACK_ENABLED=true`. The application will attempt its normal provider wrapper, whose chat and embedding fallbacks are Gemini; the collector records that provider chain instead of claiming an unsupported direct-provider mode. The collector validates these settings before it writes an execution bundle.

## Required real-RAGAS inputs

Each sample needs a question, a reviewed reference answer, EduGround's generated response, and the exact retrieved contexts sent to the evaluator. `reference_contexts` identify the approved source evidence; they do not replace the live retrieved contexts.

The primary metrics are:

- `faithfulness`: whether claims in the generated response are supported by the retrieved context.
- `context_recall`: whether the retrieved context contains the information needed by the reviewed reference answer.

`context_precision` and `answer_correctness` may be reported as secondary metrics only when their required inputs and evaluator settings have also been captured.

## Run retention

Every executed run gets its own immutable folder under `runs/`. Retain the dataset, source manifest, retrieval configuration, evaluator configuration, raw model inputs, raw per-sample scores, aggregate results, command output, dependency lock or `pip freeze`, Docker image identifiers, and code revision. Never store API keys, bearer tokens, or `.env` files.

The current `RetrievalTrace.query_embedding_model` field is populated from the OpenRouter embedding setting even after a Gemini fallback. Therefore, the run collector must record the actual provider and model from the controlled run configuration and provider logs; it must not treat that database field as proof of the embedding provider used.

Use `runs/README.md` as the required file list. The dataset and outputs may contain course material, so keep this repository private or apply the institution's data-retention rules before sharing it.
