# Local dataset review workspace

Built now: a standalone, table-first reviewer for current Eduground RAGAS dataset JSON files. No new dependencies, database migration, provider call, or API key required. It does not upload files or publish approvals to the student application.

## Start

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe tools\evaluation\review\serve.py
```

Open <http://127.0.0.1:8766>. Use this exact host/port for consistent browser-draft storage. The server binds only to loopback and serves only this tool directory, not the repository or `.env`. Use Ctrl+C to stop a foreground server. `--port 8767` selects another port (a separate browser-storage origin).

## Workflow

1. Import one or more dataset `.json` files containing `samples`. Existing files under `evaluation/ragas/datasets/` work; `eduground_100q.gemini-v1.draft.json` is the existing CSC442 draft. For v2 datasets, also import the matching evidence packet from `export_course_evidence.py` to see complete indexed chunks. You may also import exported review bundles.
2. Enter your reviewer name. Select a question ID to open the editor.
3. Set the course code on first review (applies to that imported dataset). Review the question, reference, exact quoted passages and source locators. Open the actual course document separately where necessary.
4. Correct question/reference/passages and enter review notes. Editing content resets the proposed decision to Pending. Then explicitly select Accept, Reject, Flag, or Pending. Accept requires source-evidence confirmation; Reject and Flag require an explanation.
5. Save the review. This creates a timestamped revision in local history and saves a browser draft where storage is available.
6. Export review bundle JSON regularly. It includes original datasets, hashes, current decisions, and review history. CSV is useful for reading or spreadsheets; it is not the round-trip audit format.
7. Reimport JSON to resume elsewhere. Reimporting a raw dataset does not replace existing decisions. Importing an existing review packet asks before replacing local reviews.

All imported legacy statuses, including `approved`, start Pending unless they are explicit reviews from this workspace's bundle format. This avoids treating previous automated source-integrity approval as human review.

Columns: Course / Question ID, Question, Decision, Corrected reference, Supporting passages, Required changes, Reviewer, UTC date. Includes course/status/text filters and 25-item pages. The example button loads explicitly illustrative text, not research evidence.

## Limits and integrity

- Your name is self-entered; this is not authenticated or externally certified review.
- Browser storage may be cleared or exhausted. JSON export is the durable backup. The workspace reports storage failures.
- JSON hashes detect accidental mismatches, not maliciously recomputed edits. No cryptographic identity or tamper-proof history is claimed.
- Original sample/source metadata is retained. Edited supporting text is a proposed correction; source existence/containment is not checked against the live database here.
- Version 2 datasets support one editable quote per source/chunk binding, including multi-paragraph quotes and unanswerable items. Legacy v1 files retain the blank-line-separated passage field. Backend release rechecks source and chunk identity, quote containment and current source versions.
- The UI shows complete indexed chunks when a matching evidence packet is imported, and checks corrected v2 quotes against them before acceptance. Evidence packets are held only in memory and must be re-imported each browser session. Open original PDFs separately when checking extraction quality; the UI does not render them.
- This export is a review proposal, not an approved dataset accepted by the existing collector. A local backend validator and source-bound release freezer now exist in `tools/evaluation/freeze_reviewed_dataset.py`; use them only after reviewing real evidence. The controlled collector remains pending.
- CSV cells are quoted and formula-like values are neutralised. Imported text is rendered as text, not executable HTML.
- No account, external fonts, analytics, CDN, or external network request is used.

## Tests

```powershell
node --test tools/evaluation/review/model.test.mjs
# With the local server running and the repository's Playwright browser installed:
node --test tools/evaluation/review/browser.test.mjs
```

Browser tests use an isolated browser context, not your personal review draft. They exercise import, decisions, edits, export/reimport, unsafe text, and mobile layout. Tests make no provider calls.
