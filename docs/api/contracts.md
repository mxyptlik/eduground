# API Contracts

This document captures the MVP contract shapes that other services should align to.

## Core entities

### Answer

```json
{
  "id": "uuid",
  "chat_message_id": "uuid",
  "answer_type": "grounded_answer",
  "content_markdown": "string",
  "citation_coverage_ratio": 0.95,
  "had_refusal": false,
  "created_at": "2026-03-17T00:00:00Z"
}
```

### Citation

```json
{
  "id": "uuid",
  "assistant_answer_id": "uuid",
  "source_id": "uuid",
  "chunk_id": "uuid",
  "source_segment_id": "uuid",
  "page_start": 1,
  "page_end": 2,
  "slide_start": null,
  "slide_end": null,
  "quote_text": "string",
  "display_label": "Lecture 3, page 2"
}
```

### Retrieval trace

```json
{
  "id": "uuid",
  "chat_message_id": "uuid",
  "query_text": "string",
  "retrieval_mode": "dense",
  "top_k_requested": 10,
  "top_k_used": 8,
  "filters_json": {
    "notebook_id": "uuid",
    "module_id": "uuid"
  },
  "reranker_used": false,
  "latency_ms": 120
}
```

### Ingestion job

```json
{
  "id": "uuid",
  "source_id": "uuid",
  "job_type": "parse",
  "status": "queued",
  "attempt_count": 0,
  "error_message": null,
  "started_at": null,
  "finished_at": null
}
```

## Public HTTP surface

The MVP plan centers on auth, notebooks, sources, chat, notes, quizzes, analytics, and admin routes. Route names and the sequencing are already captured in the architecture package; this document is the contract anchor for future implementation.

