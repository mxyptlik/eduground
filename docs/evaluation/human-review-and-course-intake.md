# Human review and 17-course intake

## Your role

You are the human subject-matter reviewer of model-authored evaluation material. You decide whether each question and reference are suitable; the software does not make that decision on your behalf.

If you are also the system developer or thesis author, describe this as "human review by the project author, independent of model generation." It is not the same as external independent review. For a claim of externally independent review, invite a qualified lecturer or colleague who was not responsible for the system/dataset. Record affiliations and conflicts honestly. The same workspace can record another reviewer, but the first local prototype does not authenticate identities.

## Supply the 17 courses

Recommended: provide an absolute directory path with one subdirectory per course. Keep original files unchanged. Do not copy private course material into tracked Git folders.

```text
C:\CourseCorpus\
  courses.csv
  CSC442\
    lecture-01.pdf
    lecture-02.pptx
  COURSE002\
    unit-01.docx
  ...
```

`courses.csv` columns:

```csv
course_id,title,relative_directory,notebook_id,reviewer,notes
CSC442,Course title,CSC442,,Your name,Original course material
```

Use real course codes; the folder tree above is illustrative. Add a README or notes identifying obsolete versions, lecturer-approved references, optional readings, and documents to exclude. Tell us which two courses should be the pilot, or we can propose contrasting ones from the inventory. Do not upload API keys.

Directory supply is an intake convenience, not a second ingestion implementation. The planned importer must create/reuse an evaluation notebook for each course, request a signed upload URL, upload the bytes, register the source, and wait for the existing worker to index it. Hash-based idempotency prevents duplicate uploads. Unsupported, failed, empty, or partially extracted documents receive visible statuses.

Alternative available now: use the normal notebook UI and upload one course per dedicated evaluation notebook. Then provide the course-to-notebook mapping. The evaluation exporter will use the indexed sources, IDs, and checksums. Do not mix all 17 courses in one notebook. Avoid personal notes in evaluation notebooks because the current collector rejects notebooks containing notes.

Both intake routes converge on the same manifest and normal application pipeline. A local directory has not yet been imported; the batch importer is a planned task. Existing uploaded courses should be reused after verifying their source versions rather than uploaded again unnecessarily.

## Responsibilities and handoffs

| Point | Your responsibility | What the system supplies | Blocking condition |
|---|---|---|---|
| Corpus intake | Confirm course identity, authoritative documents, exclusions, permitted use | File inventory, extraction failures, duplicates | Unclear ownership/course mapping |
| Protocol selection | Agree question allocation, course coverage, test split, API budget, service targets | Proposed design and workload estimates | Full runs wait for frozen protocol, not a new permission request on every tool call |
| Dataset review | Read each question, reference, and actual supporting source | Course/question table, passages and locators, full-context evidence in later exporter | Only accepted current revisions become eligible |
| Relevance annotation | Identify alternative support and multi-passage requirements | Candidate passages with IDs | Required for exhaustive Recall@k/nDCG claims |
| Unanswerability | Verify missing evidence across the frozen corpus | Searchable source inventory | A retrieval miss alone is insufficient |
| Dataset freeze | Confirm accepted set is ready | Coverage report, unresolved flags, hash manifest | Rejected/flagged/pending rows excluded and counted |
| Judge calibration | Independently label selected answers/citation claims before viewing model judgments | Blind review packet and rubric | Disagreements reviewed before final scoring protocol freezes |
| Results audit | Inspect failures and surprising scores | Stratified samples and judge explanations | Do not edit held-out references merely to improve scores |
| Final report | Confirm methodological statements and limitations | Per-course metrics, distributions, costs, failures | No claim of external independence without an external reviewer |

## Per-question checklist

1. Is the question unambiguous, standalone, and relevant to the course?
2. Is its wording free of accidental answers, misleading assumptions, or dependence on previous questions?
3. Is the reference factually correct, sufficiently complete, and bounded to the supplied sources?
4. Do the supporting passages establish all required facts? Check the original document/page, not only an AI-picked quotation.
5. Are source IDs, chunk IDs, page/slide locators, and quotes correct?
6. Are alternative correct answers/evidence recognised? Is a multiple-passage question incorrectly tied to only one chunk?
7. Are question type, difficulty, and answerability accurate?
8. Is this distinct from other items and suitable for the allocated split?

Decision meanings:

- **Accept:** this exact question/reference/evidence revision is suitable; reviewer identity and review timestamp required.
- **Reject:** exclude this item; explain the defect. Replacement authoring creates a new draft rather than erasing the rejection.
- **Flag:** uncertain or awaiting correction/expert review; not eligible for a final run.
- **Pending:** not yet reviewed; never eligible for a final run.

If you correct a question, reference, or evidence, inspect the final revision and accept it explicitly. Later edits invalidate acceptance. Automated integrity checking is a separate status and cannot replace your decision.

## Using the lightweight workspace now

See [workspace instructions](../../tools/evaluation/review/README.md). Import a current dataset JSON or several course datasets. Set a course code and reviewer, inspect each question and its displayed evidence, edit the corrected fields, then save a decision. Filter by course or decision and search by question text/ID. Export the JSON review bundle as your durable handoff; CSV is a reading/reporting copy.

Legacy `approved` statuses are intentionally imported as Pending: historical automatic approval is not a new human decision. Existing source metadata and original samples remain intact in the bundle. The prototype does not publish an approved dataset or update the database. A planned backend validator will verify bundle hashes, evidence, and revisions before freeze.

The current prototype shows evidence contained in the imported JSON. If it only contains short quotes, open the original documents separately; full-chunk/source preview is a planned exporter/UI extension. A review bundle imported from another person is self-reported evidence, not a cryptographic identity assertion.

## Workload for 17 courses

Start with 20 questions each on two courses (40 total). At 100 per course the primary set becomes 1,700 questions. At an illustrative 3-5 minutes per question that is about 85-142 hours, excluding repairs and calibration. This is arithmetic for planning, not a measured review rate.

Time the pilot before fixing per-course counts. Permit variable counts, document coverage rationale, and report equal-course macro averages as well as question-weighted summaries. Schedule manageable batches (for example 10-20 questions), not a single 1,700-row approval exercise. Prioritise domain expertise over speed; flag courses outside your competence for another reviewer.

## Needed from you next

1. The course directory path, or a list mapping 17 course codes to existing notebook IDs.
2. Your reviewer display name and whether a lecturer/second reviewer will participate.
3. The exact Gemini, Kimi, and DeepSeek versions you prefer, if already chosen. Otherwise capability testing will produce a concrete selection before paid runs.
4. The pilot/full-run spending ceiling and intended deployment hardware before operational experiments.

None of these prevent building contracts, the review tool, or offline tests now. Dataset generation needs source intake; paid model experiments need pinned model IDs and a budget.
