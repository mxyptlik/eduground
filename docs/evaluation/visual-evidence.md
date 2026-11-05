# Visual evidence layer: text cards from figures, charts and scans

Status 2026-09-30: implemented and offline-tested; **off by default**; no vision provider call or real-course ingestion has been made. This layer is an addition beyond the planned programme; the T04/T06 studies in `programme-scope.md` remain the place where its value is measured.

## What it does

Figures, charts, drawings and scanned pages become **text cards** — ordinary normalized segments — so the existing normalize → chunk → embed → index → retrieve → cite path works unchanged. Cards carry locators (`page N` / `slide N`, figure ordinal, bbox) and start with the marker `[figure]`, which lets reports separate figure-derived evidence from body text.

## Layers

1. **Detection (deterministic, free)** — `apps/worker/app/services/figures.py`.
   - PDF (pymupdf): embedded images with bboxes and SHA-256; vector drawing regions rendered to PNG when a page has enough paths; full-page-image pages flagged as scans; captions/nearest text attached.
   - PPTX (python-pptx): pictures (including inside groups), charts; **chart categories/series are converted to a markdown table directly from the file** — no model call; speaker/slide text kept as context. Table shapes are not treated as figures (they are already extracted as text).
   - **Image formats**: PNG/JPEG/WebP/GIF pass through unchanged; formats PyMuPDF can decode are re-encoded to PNG; long edges are shrunk to 2048 px. PDF images in exotic codecs (JBIG2/CCITT/JPEG2000) fall back to rendering the page region, so vision always receives a readable image. Office metafiles and SVG (EMF/WMF/SVG, common in PowerPoint) have no local renderer: the figure is kept, counted as `unsupported_format`, and its card states `[visual content not described] (image/x-emf has no local renderer)` — visible in the evaluation, never silently dropped. The original MIME is kept as provenance on every record.
   - All failures are contained: a damaged file yields an empty inventory and ingestion proceeds on text alone.
2. **Local fidelity** — audit-based OCR routing: when the layer is enabled, a PDF whose pages are ≥50% full-page scans triggers the existing OCR fallback even when a thin text layer exists (`scanned_page_ratio`). Table segments are already atomic through the chunker; multi-column reading order remains a T04 measurement, not a claim.
3. **Vision understanding (paid, ledger-guarded)** — `apps/worker/app/services/vision.py`.
   - Provider-agnostic: `openrouter` (chat completions with data-URL images; preferred) or `gemini` (inline_data). The provider/model pair is pinned; the returned identity and usage are verified through the shared budget ledger under operation `visual_understanding`, then settled. Silent provider fallback does not exist.
   - Strict JSON contract: `figure_type`, `verbatim_text`, `table_markdown`, `description`, `relationships`, `uncertainty`. Malformed output is a failure, never a partial card.
   - Identical images are described once: the library cache is keyed by image SHA-256 + prompt version + provider + model, so the same diagram across many slides is paid for once. Cache writes are atomic and optional.
   - Failure isolation: provider errors, oversize images and cache problems leave the figure as `[visual content not described]`; ingestion never fails because of vision.
4. **Cards to segments** — `apps/worker/app/services/visual.py` inserts one segment per kept figure after the last segment of its page/slide, so `page`/`slide` metadata, citations, evidence packets and the review workspace keep working with no schema change. Vision is skipped entirely when the shared ledger is not active (no unledgered paid calls).
5. **Evaluation protocol** — to measure the layer honestly:
   - Ingest the same frozen course corpus twice: visual layer off and on (same embedding and chunking settings). Keep both evidence packets.
   - Ask the same reviewed questions in both, using the same conditions; compare retrieval metrics, context recall/faithfulness (RAGAS), refusal rates and retrieved-context composition.
   - Report figure-derived evidence share using the `[figure]` marker, plus vision cost by role and per course/document, and the count of `not_described` figures.
   - Known limits: description fidelity is model-dependent (verbatim-first prompt, `uncertainty` field, and human spot-checks in the review workspace are the mitigations); faithfulness scoring is text-based, so image-only claims are not judgeable until a multimodal judge exists; PPTX vector drawings (SmartArt/connectors without embedded images) have no pixels to send and currently yield data-free cards.

## Configuration (environment of the dedicated evaluation worker)

- `CURRICULUM_TUTOR_VISUAL_ENABLED` (default `false`)
- `CURRICULUM_TUTOR_VISUAL_PROVIDER` (`openrouter` | `gemini`, pin after a capability probe)
- `CURRICULUM_TUTOR_VISUAL_MODEL` (exact ID)
- `CURRICULUM_TUTOR_VISUAL_CACHE_DIR` (default `.eduground_visual_cache`)
- `CURRICULUM_TUTOR_VISUAL_MAX_FIGURES_PER_DOCUMENT`, `..._MAX_IMAGE_BYTES`, `..._MIN_AREA_FRACTION`, `..._MAX_OUTPUT_TOKENS`

Price registry entries needed for live vision calls: `<provider> / <vision model> / visual_understanding` with `input_tokens` and `output_tokens` (images are billed as input tokens).
