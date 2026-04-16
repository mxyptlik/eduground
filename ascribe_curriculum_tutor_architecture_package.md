# NotebookLM-Like, RAG-Powered Curriculum Tutoring System
## Architecture Breakdown, Development Roadmap, Database Design, Full Implementation Plan, and Agent Prompt

## 0. Executive framing

This package translates the PRD into an implementation-grade product and engineering blueprint for a **NotebookLM-like, curriculum-grounded tutoring system**. The PRD defines a platform where course materials are ingested into notebook-style workspaces, then used to power grounded tutoring, notes, quizzes, and progress tracking with mandatory citations, role-based access control, and institution-first governance. The product is explicitly **RAG-first**, not fine-tuning-first, and targets an MVP with PDF/DOC/PPT ingestion, OCR fallback, notebook/source management, grounded chat, source scoping, notes, quiz generation, progress tracking, and RBAC. fileciteturn2file0

The PRD also sets the key operating assumptions that should shape the design: around **10,000 registered users**, **2,000 DAU during peak periods**, **~1,000,000 indexed chunks**, **~50,000 source documents**, **p95 end-to-end grounded chat latency of 6 seconds or less**, and deployment paths that support both cloud and on-prem operation. fileciteturn2file0

Based on that, the best current-day industry-standard interpretation is:

- Use a **service-oriented modular monolith first**, with clean internal boundaries and event-driven background workers.
- Use **PostgreSQL** as the system of record for metadata, authorization, analytics aggregates, and workflow state.
- Use a **vector database with strong metadata filtering** because notebook, course, week, module, and role scoping are first-class requirements in the PRD. Qdrant explicitly documents filterable HNSW and adaptive query planning for filtered retrieval workloads, which fits this system well. citeturn598145view1
- Use a **RAG evaluation loop** with at least faithfulness and context recall because the PRD explicitly names RAGAS-style evaluation; Ragas defines faithfulness as factual consistency of a response with retrieved context and context recall as whether the retriever actually surfaced the relevant evidence. fileciteturn2file0 citeturn598145view2turn598145view3
- Use **LTI 1.3** as the LMS integration path because the PRD calls for it and the standard explicitly exists to integrate external tools with LMS platforms using the modern security model. fileciteturn2file0 citeturn598145view4
- Use a modern API abstraction that can support structured tool usage, long-running conversations, and robust production controls; the OpenAI Responses API remains an actively evolving official path with support for capabilities such as tool search, WebSocket mode, and server-side compaction in 2026. citeturn598145view0

---

## 1. Clean system architecture breakdown

## 1.1 Architectural style

### Recommended architecture style

Use a **modular monolith with asynchronous workers** for the first production-grade version.

That means:
- one deployable backend application for core synchronous APIs,
- one worker process pool for asynchronous ingestion and batch jobs,
- dedicated infrastructure services for object storage, PostgreSQL, Redis, vector DB, and observability,
- internal module boundaries that can later be extracted into microservices if scale or team topology demands it.

### Why this is the right shape

The PRD requires multiple capabilities, but the scale is still moderate enough that a microservice-heavy architecture would introduce too much operational complexity too early. The system needs strong consistency across notebooks, sources, ACLs, notes, quizzes, citations, and audit logs. A modular monolith gives you:
- faster development,
- easier local testing,
- fewer cross-service consistency problems,
- simpler transaction handling,
- cleaner security and access-control enforcement.

At the same time, ingestion, embedding generation, OCR, export, and analytics backfills are naturally asynchronous, so those should run in a queue-driven worker subsystem.

### Bounded contexts

Split the backend into these logical domains:

1. **Identity and access**
   - users
   - organizations / institutions
   - roles and notebook ACLs
   - SSO / LTI / roster sync
   - audit logging hooks

2. **Notebook and curriculum domain**
   - notebooks
   - courses / terms / sections
   - modules / weeks / learning objectives
   - notebook policies and mode settings

3. **Content ingestion domain**
   - uploads/imports
   - parsing / OCR
   - normalization
   - chunking
   - embedding jobs
   - indexing lifecycle
   - dedup/versioning

4. **Retrieval and tutoring domain**
   - query preprocessing
   - scoping and filtering
   - retriever
   - reranker
   - prompt assembly
   - LLM inference
   - citation formatting
   - refusal/insufficient-evidence handling

5. **Knowledge artifact domain**
   - notes
   - study packs
   - saved answers
   - exports

6. **Assessment and progress domain**
   - quiz generation
   - quiz items
   - attempts
   - mastery tracking
   - explain-why feedback

7. **Observability and quality domain**
   - metrics
   - traces
   - retrieval traces
   - golden evaluation sets
   - model version registry
   - A/B prompt and model experiments

---

## 1.2 High-level component architecture

### A. Client applications

#### Web app
Primary product surface.

Recommended stack:
- Next.js or React SPA with server rendering where useful
- TypeScript
- component library for consistency and accessibility
- markdown and rich text support for notes
- PDF/slide source viewer with page/slide anchored citation navigation

Primary UI surfaces:
- notebook dashboard
- source library
- source upload/import
- tutor chat
- citation preview drawer
- notes editor
- quiz player
- mastery dashboard
- instructor analytics
- admin governance console

#### Mobile app
Optional after the web experience is stable. The PRD’s MVP does not require native mobile.

#### LMS embedded view
An LTI 1.3 launchable surface for Canvas/Moodle/Blackboard-like embedding. The standard exists specifically for LMS-to-tool integration with a stronger security model in v1.3. citeturn598145view4

### B. Edge and API layer

#### API gateway / edge
Responsibilities:
- route requests
- terminate TLS
- inject request IDs
- enforce rate limiting
- basic WAF rules
- auth token verification
- request body limits for uploads

Use:
- NGINX / Envoy / managed cloud ingress
- CDN only for static assets and public docs, not sensitive source content

#### Backend application API
Expose:
- REST for standard CRUD and workflows
- WebSocket or SSE for streaming tutor answers and ingestion status
- signed URL endpoints for file upload/download

### C. Core application modules

#### Auth and RBAC module
Responsibilities:
- email/password or SSO login
- JWT/session issuance
- role and notebook ACL enforcement
- institution membership
- LTI session bootstrap
- audit event emission

The PRD explicitly calls for student/TA/instructor/admin roles and audited access. fileciteturn2file0

#### Notebook service
Responsibilities:
- notebook lifecycle
- course metadata
- visibility and policy modes
- learning-objective mapping
- source scopes
- roster associations

#### Source library service
Responsibilities:
- upload/import source records
- source versioning
- dedup using checksum/fingerprint
- source metadata editing
- source activation/deactivation
- delete and retention workflows

#### Ingestion orchestrator
Responsibilities:
- create ingestion jobs
- dispatch parse/OCR/chunk/embed/index stages
- maintain retry and dead-letter policies
- surface progress and errors back to the UI

#### Retrieval service
Responsibilities:
- scope enforcement by notebook/module/week/source
- query transformation
- hybrid search control
- metadata filtering
- reranking
- duplicate suppression
- retrieval trace persistence

The PRD requires filtering by notebook/source scope and module/week metadata, and calls out HNSW and MMR-style retrieval optimization. fileciteturn2file0

#### Tutor orchestration service
Responsibilities:
- policy-mode aware prompt building
- context budgeting
- answer generation
- citation coverage validation
- insufficient-evidence refusal flow
- answer persistence
- saved-answer to note conversion

#### Notes service
Responsibilities:
- manual notes
- saved answers
- collaborative notes later
- note-to-source conversion
- export pipeline

#### Quiz service
Responsibilities:
- grounded quiz generation
- source-linked rationales
- attempt capture
- scoring
- per-topic mastery calculations

#### Analytics and admin service
Responsibilities:
- common questions
- citation failure rates
- weak topic detection
- notebook adoption
- audit review
- policy/configuration management

### D. Background workers

Use an async queue for:
- OCR jobs
- slide/PDF parsing
- audio transcription later
- chunk generation
- embedding generation
- vector upserts
- export generation
- analytics materialization
- evaluation harness runs

This should be a separate worker deployment but share the same codebase where possible.

### E. Data and infra services

#### PostgreSQL
System of record for:
- users
- orgs
- notebooks
- source metadata
- curriculum mappings
- ACLs
- notes
- quiz metadata
- attempts
- audit logs
- model and prompt versions
- evaluation results

#### Vector database
Recommended: **Qdrant**.

Why:
- the product depends on strong metadata filtering by notebook, course, module, week, source, policy, and access scope;
- Qdrant documents filterable HNSW and automatic query-planning behavior for filtered search, which matches this workload well. citeturn598145view1

Alternatives:
- Weaviate if hybrid retrieval is the main differentiator
- pgvector if you want fewer moving parts and modest scale
- Milvus for larger infra-heavy deployments

#### Object storage
Use S3-compatible object storage for:
- raw uploads
- normalized text artifacts
- OCR text artifacts
- export bundles
- preview derivatives

#### Redis
Use for:
- short-lived session data
- ingestion progress cache
- stream assembly state
- idempotency keys
- rate limit counters
- query result cache where safe

#### Search index
Optional secondary lexical index for hybrid retrieval.

Two acceptable approaches:
- use vector DB sparse/hybrid capabilities if chosen DB supports it well,
- or add Elasticsearch/OpenSearch later when hybrid search becomes a hard requirement.

#### Observability stack
Use:
- OpenTelemetry for traces
- Prometheus/Grafana for metrics
- centralized log store
- error tracking

The PRD explicitly calls for per-request tracing and retrieval/generation metrics. fileciteturn2file0

---

## 1.3 End-to-end request flows

## Flow 1: Source ingestion

1. Instructor uploads a file or imports from a connector.
2. API creates `source` and `ingestion_job` records.
3. Raw file stored in object storage.
4. Queue dispatches parse worker.
5. Parser extracts text; if text density is too low, OCR worker is invoked.
6. Normalizer preserves page/slide boundaries and structural markers.
7. Chunker creates semantically bounded chunks around headings/slide titles, with target chunk sizes aligned to the PRD’s suggested 300–500 token range. fileciteturn2file0
8. Embedder generates vectors.
9. Vector upsert writes points into vector DB with payload filters.
10. Metadata records and chunk records are committed in PostgreSQL.
11. Ingestion job marked `completed`, `completed_with_warnings`, or `failed`.
12. Searchability becomes available in notebook UI.

## Flow 2: Grounded tutoring answer

1. Student selects notebook and optional source/week/module filters.
2. API validates notebook access and scoping rights.
3. Tutor service normalizes the query and determines policy mode.
4. Retrieval service executes vector or hybrid search with strict payload filters.
5. Reranker reorders candidates and removes near duplicates.
6. Context builder enforces token budget and assembles evidence blocks.
7. LLM generates answer under citation rules and policy instructions.
8. Citation validator checks each factual segment is backed by source references.
9. If evidence is insufficient, answer is converted to a refusal/redirect asking for additional sources.
10. Stream answer to user.
11. Persist conversation turn, retrieval trace, model version, and citations.

## Flow 3: Quiz generation

1. Student or instructor selects notebook and scope.
2. Retrieval service fetches evidence for the requested topic or module.
3. LLM generates quiz items constrained to retrieved evidence.
4. Citation validator ensures each question and rationale is evidence-backed.
5. Quiz stored with immutable item set and provenance.
6. Student submits attempt.
7. Scoring engine evaluates objective items and records answers.
8. Feedback generator creates cited explanations.
9. Mastery service updates topic proficiency estimates.

## Flow 4: Note to source conversion

1. Student selects notes or saved answers.
2. System packages them into a synthetic source document.
3. Synthetic source is versioned and marked as `derived_from_note`.
4. Content is chunked and indexed like any other source.
5. Future tutoring can optionally include or exclude these study-pack sources.

---

## 1.4 Current-day industry-standard technical decisions

### Core backend
- **Python + FastAPI** for backend APIs, orchestration, and ML-adjacent workflows.
- **Celery / Dramatiq / Temporal-like orchestration** for asynchronous ingestion and durable jobs.
- **Pydantic** for schema contracts.
- **SQLAlchemy / SQLModel / Prisma-equivalent if using TypeScript stack** for DB access.

Why Python:
- best fit for OCR, parsing, embeddings, evaluation, and RAG tooling.
- simplifies background ML and data processing tasks.

### Frontend
- **Next.js + TypeScript**.
- Rich source viewer for PDFs/slides with page anchors.
- Streaming chat UX.
- State management only as needed; do not over-engineer.

### LLM layer
Recommended deployment abstraction:
- `LLMProvider` interface supporting hosted and self-hosted providers.
- `EmbeddingProvider` interface.
- `RerankerProvider` interface.

Why abstraction matters:
- the PRD explicitly leaves room for hosted or self-hosted models and on-prem deployment. fileciteturn2file0
- OpenAI’s Responses API is actively evolving with features that help agentic production systems, but institutions may still require alternative or self-hosted routes. citeturn598145view0

### Retrieval
Recommended progression:
- Phase 1: dense retrieval + strict metadata filters
- Phase 2: dense + BM25/sparse hybrid
- Phase 3: reranker tuning and query rewriting

Reason:
- dense-only is fastest to ship;
- hybrid improves recall, especially for course-specific terms, formulas, abbreviations, and slide keywords;
- the PRD already treats hybrid search as nice-to-have rather than mandatory MVP. fileciteturn2file0

### Security and auth
- SSO-ready from day one
- RBAC + notebook ACLs
- audit logs on sensitive actions
- short-lived signed URLs for private file access
- privacy mode that avoids raw student query logging by default, matching the PRD guidance. fileciteturn2file0

### Performance design
- async ingestion
- incremental indexing
- precomputed previews
- cached notebook metadata
- streaming LLM responses
- retrieval target under 300 ms per PRD
- end-to-end p95 under 6 seconds per PRD. fileciteturn2file0

---

## 1.5 Architecture decisions register

### ADR-001: RAG-first over fine-tuning-first
**Decision:** Ground all answers through retrieval from notebook sources.

**Reason:** The PRD explicitly defines the system this way and requires citations and transparent provenance. fileciteturn2file0

### ADR-002: Modular monolith before microservices
**Decision:** Keep one backend application with strong modules plus worker tier.

**Reason:** Faster delivery, easier testing, lower ops burden, and sufficient for expected scale.

### ADR-003: PostgreSQL as source of truth
**Decision:** Keep metadata, policies, ACLs, progress, and evaluations in Postgres.

**Reason:** Strong transactions and rich queryability across notebook workflows.

### ADR-004: Qdrant as primary vector store
**Decision:** Use Qdrant for filtered ANN retrieval.

**Reason:** The workload is heavily filter-dependent, and Qdrant documents filterable HNSW and adaptive query planning for this class of problems. citeturn598145view1

### ADR-005: Mandatory citation validator in serving path
**Decision:** Do not trust model output alone; validate citation presence and provenance before final response.

**Reason:** The PRD makes inspectable citations and insufficient-evidence behavior a core requirement. fileciteturn2file0

### ADR-006: Evaluation harness is a product dependency, not an afterthought
**Decision:** Build automated RAG evaluation and regression testing into the platform.

**Reason:** The PRD requires RAGAS-style evaluation and model pinning. Ragas explicitly defines faithfulness and context recall, which are directly applicable here. fileciteturn2file0 citeturn598145view2turn598145view3

---

## 2. Development roadmap

The PRD provides an indicative sequence. Here it is translated into a more practical engineering roadmap without calendar dates, as requested. fileciteturn2file0

## 2.1 Roadmap principles

1. **Ship the grounded core before convenience features.**
2. **Make evaluation and observability first-class from the beginning.**
3. **Delay multimodal expansion until text grounding is strong.** This follows the PRD’s text-first MVP recommendation. fileciteturn2file0
4. **Design for policy and governance early**, even if the first UI is simple.
5. **Treat notes, quizzes, and progress as downstream artifacts of retrieval quality.** If retrieval is weak, those features will also be weak.

## 2.2 Capability roadmap

### Phase 0: Foundations and product hardening rules

Deliverables:
- final domain model
- notebook lifecycle rules
- policy modes definition
- permission matrix
- source metadata standard
- citation contract
- retrieval trace contract
- evaluation dataset specification
- golden question format
- model/version registry design

Exit criteria:
- every major domain has owner-approved invariants
- answer object format and citation object format are frozen
- insufficient-evidence behavior is defined

### Phase 1: Platform core and governance skeleton

Deliverables:
- auth
- RBAC
- org and institution model
- notebook CRUD
- source library CRUD
- audit logging baseline
- signed upload flow
- base admin console

Exit criteria:
- student/TA/instructor/admin permissions work end-to-end
- notebook isolation is enforced
- uploads and deletions are audited

### Phase 2: Ingestion and indexing engine

Deliverables:
- ingestion job orchestration
- parser adapters for PDF, DOCX, PPTX, TXT/MD
- OCR fallback
- normalization and structural metadata extraction
- chunking pipeline
- embedding generation pipeline
- vector upsert pipeline
- source status and error reporting UI

Exit criteria:
- uploaded materials become searchable
- page/slide provenance is preserved
- re-indexing on new source versions works
- source delete triggers derived data cleanup

### Phase 3: Retrieval engine and citation substrate

Deliverables:
- strict notebook/source/module/week filtering
- dense retrieval
- retrieval trace persistence
- reranking with redundancy control
- chunk deduplication
- citation provenance schema
- citation renderer and preview UX contract

Exit criteria:
- retriever reliably returns scoped relevant chunks
- citations point to source/page/slide/snippet
- answer pipeline can enforce citation coverage

### Phase 4: Tutor serving experience

Deliverables:
- streaming tutor chat
- policy modes
- conversation memory rules
- context budgeting
- insufficient-evidence refusal logic
- saved-answer-to-note action
- source inclusion/exclusion UX

Exit criteria:
- users can ask scoped questions and inspect evidence
- system refuses when evidence is not sufficient
- conversation history does not leak across notebooks

### Phase 5: Notes and study-pack workflows

Deliverables:
- notes editor
- save tutor response to note
- note organization
- note export
- note-to-source conversion

Exit criteria:
- study pack flow works end-to-end
- notes can be reused as optional retrieval sources

### Phase 6: Quizing and mastery

Deliverables:
- quiz generation service
- MCQ and short-answer templates
- source-grounded explanations
- attempt workflow
- scoring engine
- topic mastery calculation
- student progress dashboard

Exit criteria:
- quiz items have citations
- attempts persist
- dashboard reflects meaningful mastery state

### Phase 7: Evaluation, safety, and production readiness

Deliverables:
- golden QA dataset tooling
- RAGAS evaluation harness
- latency/load test harness
- prompt regression tests
- role/policy security tests
- observability dashboards
- admin analytics
- privacy controls and retention policies

Exit criteria:
- faithfulness and recall meet target ranges for pilot
- latency meets service-level objectives
- audit and retention behavior is verified

### Phase 8: Integrations and expansion

Deliverables:
- LTI 1.3 integration
- roster sync
- hybrid retrieval
- collaboration
- audio transcript ingestion
- image-aware figure support
- self-hosted deployment packaging

Exit criteria:
- LMS embedded launch works
- hybrid improves retrieval on keyword-heavy queries
- privacy-sensitive institutions can choose non-cloud serving paths

---

## 2.3 Delivery workstreams

### Workstream A: Product and UX
- notebook IA
- chat and citation UX
- source viewer UX
- notes and quiz UX
- instructor analytics UX

### Workstream B: Platform and backend
- auth
- notebook/source APIs
- orchestration
- exports
- admin controls

### Workstream C: Retrieval and ML
- parsers
- chunkers
- embeddings
- vector config
- retriever
- reranker
- evaluation suite

### Workstream D: Security and compliance
- RBAC
- auditing
- retention
- privacy mode
- SSO
- deployment hardening

### Workstream E: DevOps and observability
- CI/CD
- environments
- metrics
- tracing
- secrets
- backups

---

## 2.4 Recommended MVP cut line

For a disciplined MVP, include exactly these:
- notebook + source library
- PDF/DOCX/PPTX/TXT ingestion
- OCR fallback for scanned PDFs
- dense retrieval with strict metadata filtering
- grounded chat with mandatory citations
- source scoping
- notes and save-to-note
- quiz generation with explanations
- progress tracking
- auth + RBAC + audit log baseline
- evaluation harness

Defer:
- real-time collaboration
- full hybrid retrieval
- image grounding
- native audio import
- advanced analytics beyond essentials
- multi-institution federation

That cut line matches the PRD’s must-have emphasis and protects the schedule from unnecessary platform creep. fileciteturn2file0

---

## 3. Database/schema design

## 3.1 Storage strategy

Use **polyglot persistence with clear ownership**:

- **PostgreSQL**: transactional metadata and business records
- **Qdrant**: embeddings and retrieval payloads
- **Object storage**: raw files, derived artifacts, exports, previews
- **Redis**: ephemeral operational state

### Ownership rule
If a record affects permissions, workflow state, policy, auditability, or analytics, it belongs in PostgreSQL even if a corresponding vector payload exists elsewhere.

---

## 3.2 Core relational schema

Below is a production-minded schema outline. Names are illustrative and should be adapted to your naming convention.

### users
Stores identities.

Fields:
- `id` UUID PK
- `institution_id` UUID FK nullable
- `email` unique
- `password_hash` nullable
- `auth_provider` enum(`local`,`oidc`,`saml`,`lti`)
- `external_subject_id` nullable
- `display_name`
- `status` enum(`active`,`invited`,`suspended`,`deleted`)
- `created_at`
- `updated_at`
- `last_login_at`

Indexes:
- unique on `email`
- index on `(institution_id, status)`

### institutions
Stores institution tenants.

Fields:
- `id` UUID PK
- `name`
- `slug` unique
- `deployment_mode` enum(`cloud`,`hybrid`,`on_prem`)
- `sso_config_json`
- `data_retention_policy_json`
- `privacy_mode` enum(`standard`,`strict`)
- `created_at`
- `updated_at`

### roles
Optional reference table if not using enums.

Fields:
- `id` PK
- `name` unique

### institution_memberships
User membership and default role within institution.

Fields:
- `id` UUID PK
- `institution_id` FK
- `user_id` FK
- `role` enum(`student`,`ta`,`instructor`,`admin`)
- `created_at`

Unique:
- `(institution_id, user_id)`

### courses
Represents course identity independent of notebook instance.

Fields:
- `id` UUID PK
- `institution_id` FK
- `course_code`
- `course_name`
- `department`
- `created_at`
- `updated_at`

Unique:
- `(institution_id, course_code)`

### course_offerings
Represents a course in a term/section.

Fields:
- `id` UUID PK
- `course_id` FK
- `term`
- `section`
- `instructor_user_id` FK nullable
- `lms_course_id` nullable
- `status` enum(`draft`,`active`,`archived`)
- `created_at`
- `updated_at`

Unique:
- `(course_id, term, section)`

### notebooks
The central user-facing container.

Fields:
- `id` UUID PK
- `institution_id` FK
- `course_offering_id` FK nullable
- `title`
- `description`
- `owner_user_id` FK
- `visibility` enum(`private`,`course`,`shared`,`institution`)
- `policy_mode` enum(`teaching`,`assignment`,`exam`)
- `model_pin_id` FK nullable
- `embedding_profile_id` FK nullable
- `active_version` integer default 1
- `created_at`
- `updated_at`
- `archived_at` nullable

Indexes:
- `(institution_id, policy_mode)`
- `(course_offering_id)`

### notebook_memberships
Notebook-level access.

Fields:
- `id` UUID PK
- `notebook_id` FK
- `user_id` FK
- `role` enum(`owner`,`editor`,`viewer`,`student`,`ta`,`instructor`)
- `granted_by_user_id` FK nullable
- `created_at`

Unique:
- `(notebook_id, user_id)`

### curriculum_modules
Course/week/module structure.

Fields:
- `id` UUID PK
- `notebook_id` FK
- `parent_module_id` FK nullable
- `title`
- `module_type` enum(`week`,`topic`,`unit`,`objective_group`)
- `sort_order`
- `created_at`

### learning_objectives
Fields:
- `id` UUID PK
- `notebook_id` FK
- `module_id` FK nullable
- `code` nullable
- `text`
- `difficulty_level` nullable
- `created_at`

### sources
Metadata for an uploaded/imported source.

Fields:
- `id` UUID PK
- `notebook_id` FK
- `module_id` FK nullable
- `source_type` enum(`pdf`,`docx`,`pptx`,`txt`,`md`,`url`,`audio_transcript`,`derived_note`)
- `title`
- `original_filename`
- `storage_key`
- `mime_type`
- `checksum_sha256`
- `byte_size`
- `language_code`
- `version_number`
- `status` enum(`uploaded`,`processing`,`indexed`,`failed`,`archived`,`deleted`)
- `is_authoritative` boolean default true
- `source_origin` enum(`upload`,`connector`,`lti`,`note_conversion`,`admin_seed`)
- `ingestion_profile_id` FK nullable
- `created_by_user_id` FK
- `created_at`
- `updated_at`
- `deleted_at` nullable

Indexes:
- `(notebook_id, status)`
- `(notebook_id, module_id)`
- `(checksum_sha256)`

### source_versions
For immutable versioning and reindexing.

Fields:
- `id` UUID PK
- `source_id` FK
- `version_number`
- `storage_key`
- `checksum_sha256`
- `status`
- `created_at`
- `created_by_user_id`

Unique:
- `(source_id, version_number)`

### source_segments
Represents page/slide/time segments at a coarse grain.

Fields:
- `id` UUID PK
- `source_id` FK
- `segment_type` enum(`page`,`slide`,`section`,`time_range`)
- `segment_number` nullable
- `title` nullable
- `start_offset` nullable
- `end_offset` nullable
- `preview_storage_key` nullable
- `created_at`

### ingestion_jobs
Tracks asynchronous processing.

Fields:
- `id` UUID PK
- `source_id` FK
- `job_type` enum(`parse`,`ocr`,`normalize`,`chunk`,`embed`,`index`,`reindex`,`export_cleanup`)
- `status` enum(`queued`,`running`,`retrying`,`completed`,`failed`,`cancelled`)
- `attempt_count`
- `error_code` nullable
- `error_message` nullable
- `started_at` nullable
- `finished_at` nullable
- `created_at`
- `trace_id` nullable

Indexes:
- `(source_id, status)`
- `(job_type, status)`

### chunks
Postgres-side metadata for retrievable units.

Fields:
- `id` UUID PK
- `source_id` FK
- `source_version_id` FK
- `module_id` FK nullable
- `chunk_index` integer
- `token_count`
- `char_count`
- `text` text
- `normalized_text` text
- `start_page` nullable
- `end_page` nullable
- `start_slide` nullable
- `end_slide` nullable
- `heading_path` jsonb nullable
- `tags` jsonb nullable
- `qdrant_point_id` text
- `created_at`

Indexes:
- `(source_id, chunk_index)`
- `(module_id)`
- GIN on `tags`

### chunk_citations
Fine-grained provenance anchors.

Fields:
- `id` UUID PK
- `chunk_id` FK
- `source_segment_id` FK nullable
- `start_char_offset`
- `end_char_offset`
- `quote_text`
- `created_at`

### chat_sessions
Conversation container.

Fields:
- `id` UUID PK
- `notebook_id` FK
- `user_id` FK
- `title` nullable
- `sharing_mode` enum(`private`,`shared`) default `private`
- `policy_mode_snapshot` enum(`teaching`,`assignment`,`exam`)
- `memory_summary` text nullable
- `created_at`
- `updated_at`
- `deleted_at` nullable

Indexes:
- `(notebook_id, user_id)`

### chat_messages
Fields:
- `id` UUID PK
- `chat_session_id` FK
- `role` enum(`system`,`user`,`assistant`,`tool`)
- `content_markdown` text
- `status` enum(`complete`,`streaming`,`failed`,`redacted`)
- `token_input_count` nullable
- `token_output_count` nullable
- `model_run_id` FK nullable
- `created_at`

### assistant_answers
Assistant-specific serving metadata.

Fields:
- `id` UUID PK
- `chat_message_id` FK unique
- `answer_type` enum(`grounded_answer`,`insufficient_evidence`,`policy_refusal`,`quiz_explanation`,`summary`)
- `citation_coverage_ratio` numeric(5,4)
- `had_refusal` boolean
- `saved_to_note_count` integer default 0
- `created_at`

### retrieval_traces
Critical for debugging and evaluation.

Fields:
- `id` UUID PK
- `chat_message_id` FK nullable
- `quiz_id` FK nullable
- `query_text`
- `query_embedding_model`
- `retrieval_mode` enum(`dense`,`hybrid`,`sparse`,`manual_scope_only`)
- `top_k_requested`
- `top_k_used`
- `filters_json` jsonb
- `reranker_used` boolean
- `latency_ms`
- `created_at`

### retrieval_trace_items
Fields:
- `id` UUID PK
- `retrieval_trace_id` FK
- `chunk_id` FK
- `initial_score` numeric(12,6)
- `rerank_score` numeric(12,6) nullable
- `rank_before`
- `rank_after` nullable
- `was_used_in_context` boolean
- `dedup_reason` nullable

### citations
Links assistant outputs to provenance.

Fields:
- `id` UUID PK
- `assistant_answer_id` FK
- `source_id` FK
- `chunk_id` FK
- `source_segment_id` FK nullable
- `start_char_offset` nullable
- `end_char_offset` nullable
- `page_start` nullable
- `page_end` nullable
- `slide_start` nullable
- `slide_end` nullable
- `quote_text` text
- `display_label`
- `created_at`

Indexes:
- `(assistant_answer_id)`
- `(source_id, chunk_id)`

### notes
Fields:
- `id` UUID PK
- `notebook_id` FK
- `author_user_id` FK
- `title`
- `content_markdown`
- `note_type` enum(`manual`,`saved_answer`,`study_pack`,`faq`,`template`)
- `source_chat_message_id` FK nullable
- `converted_source_id` FK nullable
- `visibility` enum(`private`,`shared_notebook`,`instructor_only`)
- `created_at`
- `updated_at`

### note_citations
Fields:
- `id` UUID PK
- `note_id` FK
- `citation_id` FK

### quizzes
Fields:
- `id` UUID PK
- `notebook_id` FK
- `created_by_user_id` FK
- `title`
- `difficulty` enum(`easy`,`medium`,`hard`,`mixed`)
- `scope_json` jsonb
- `generation_mode` enum(`tutor_generated`,`instructor_authored`,`hybrid`)
- `status` enum(`draft`,`published`,`archived`)
- `created_at`

### quiz_items
Fields:
- `id` UUID PK
- `quiz_id` FK
- `item_type` enum(`mcq`,`short_answer`,`true_false`)
- `prompt_text`
- `options_json` jsonb nullable
- `correct_answer_json` jsonb
- `rationale_markdown` text
- `position`
- `created_at`

### quiz_item_citations
Fields:
- `id` UUID PK
- `quiz_item_id` FK
- `citation_id` FK nullable
- `source_id` FK
- `chunk_id` FK

### quiz_attempts
Fields:
- `id` UUID PK
- `quiz_id` FK
- `user_id` FK
- `started_at`
- `submitted_at` nullable
- `score_numeric` nullable
- `score_percent` nullable
- `status` enum(`in_progress`,`submitted`,`graded`,`abandoned`)

### quiz_attempt_items
Fields:
- `id` UUID PK
- `quiz_attempt_id` FK
- `quiz_item_id` FK
- `submitted_answer_json`
- `is_correct` nullable
- `feedback_markdown` text nullable
- `earned_points` nullable
- `answered_at` nullable

### topic_mastery
Materialized learning state.

Fields:
- `id` UUID PK
- `user_id` FK
- `notebook_id` FK
- `module_id` FK nullable
- `learning_objective_id` FK nullable
- `mastery_score` numeric(5,4)
- `evidence_count` integer
- `last_evaluated_at`

Unique:
- `(user_id, notebook_id, module_id, learning_objective_id)`

### model_profiles
Captures serving profiles.

Fields:
- `id` UUID PK
- `name`
- `llm_provider`
- `llm_model_name`
- `embedding_provider`
- `embedding_model_name`
- `reranker_model_name` nullable
- `context_limit`
- `status` enum(`active`,`deprecated`,`disabled`)
- `created_at`

### model_runs
An invocation record.

Fields:
- `id` UUID PK
- `model_profile_id` FK
- `operation_type` enum(`chat`,`quiz_generation`,`rerank`,`evaluation`,`summary`)
- `provider_request_id` nullable
- `latency_ms`
- `prompt_version_id` FK nullable
- `created_at`

### prompt_versions
Fields:
- `id` UUID PK
- `name`
- `purpose`
- `version`
- `template_text`
- `created_at`
- `created_by_user_id`

### evaluations
Stores automated evaluation runs.

Fields:
- `id` UUID PK
- `notebook_id` FK nullable
- `model_profile_id` FK
- `evaluation_type` enum(`ragas`,`latency`,`security`,`golden_qa`,`ab_test`)
- `status` enum(`queued`,`running`,`completed`,`failed`)
- `started_at`
- `completed_at` nullable

### evaluation_results
Fields:
- `id` UUID PK
- `evaluation_id` FK
- `sample_key`
- `faithfulness_score` numeric(5,4) nullable
- `context_recall_score` numeric(5,4) nullable
- `answer_relevance_score` numeric(5,4) nullable
- `latency_ms` nullable
- `pass_fail` boolean nullable
- `details_json` jsonb

### audit_logs
Fields:
- `id` UUID PK
- `institution_id` FK nullable
- `actor_user_id` FK nullable
- `action_type`
- `resource_type`
- `resource_id`
- `notebook_id` FK nullable
- `metadata_json` jsonb
- `created_at`
- `ip_address` inet nullable
- `user_agent` text nullable

### retention_policies
Optional explicit policy store.

Fields:
- `id` UUID PK
- `institution_id` FK
- `resource_type`
- `retention_days`
- `delete_strategy` enum(`soft_delete`,`hard_delete`,`redact`)
- `created_at`

---

## 3.3 Vector schema design

In Qdrant, each point should represent one retrievable chunk.

### Point payload fields
- `chunk_id`
- `source_id`
- `source_version_id`
- `notebook_id`
- `institution_id`
- `course_offering_id`
- `module_id`
- `learning_objective_ids`
- `source_type`
- `is_authoritative`
- `policy_scope` if needed
- `page_start`
- `page_end`
- `slide_start`
- `slide_end`
- `heading_path`
- `language_code`
- `created_at_ts`

### Why payload richness matters
The PRD’s retrieval requirements depend on exact scoping by notebook/source/module/week/access permission. That means vector similarity alone is not enough; payload filtering is part of the retrieval contract. fileciteturn2file0 Qdrant’s filtering and filterable HNSW approach make this a strong fit. citeturn598145view1

### Collection strategy
Use one of two models:

#### Option A: Single multi-tenant collection with strong payload filters
Best when:
- operational simplicity matters
- tenant count is moderate
- infra cost needs to stay low

#### Option B: Per-institution or per-large-tenant collections
Best when:
- data sovereignty boundaries are strict
- on-prem and cloud tenants coexist
- very large institutions need independent tuning

Recommended starting point:
- single collection for one institution pilot
- move to per-institution collections when tenancy expands

---

## 3.4 Redis data model

Use short TTL keys for:
- streaming answer buffers
- ingestion progress
- export status
- per-user rate-limit counters
- ephemeral citation-preview caches
- idempotency request hashes

Do not store long-lived business truth in Redis.

---

## 3.5 Deletion and retention model

The PRD explicitly expects retention controls, deletable chat options, and delete-on-source-delete behavior for embeddings and chunks. fileciteturn2file0

### Deletion rules
- deleting a source soft-deletes the source immediately
- raw object becomes inaccessible immediately
- background cleanup removes chunks, vector points, previews, and derivative artifacts
- citations to deleted sources remain only as broken historical references if audit rules require it; otherwise redact them too
- deleting a notebook cascades through all attached resources according to institution policy
- strict privacy mode should avoid keeping raw student queries in logs by default, as the PRD recommends. fileciteturn2file0

---

## 4. Full implementation plan

This section is the execution blueprint.

## 4.1 Recommended stack

### Frontend
- Next.js
- TypeScript
- Tailwind or equivalent design system
- React Query or equivalent
- TipTap/ProseMirror or Markdown editor for notes
- PDF.js or equivalent viewer for source navigation

### Backend
- Python 3.12+
- FastAPI
- Pydantic
- SQLAlchemy + Alembic
- Celery or Dramatiq for workers
- Redis for queue broker/cache

### Data and infra
- PostgreSQL 16+
- Qdrant
- S3-compatible object storage
- Redis
- OpenTelemetry
- Prometheus + Grafana

### AI layer
- embedding provider interface
- LLM provider interface
- reranker interface
- parser adapter interface
- evaluation runner

### Security and deployment
- Docker
- Kubernetes or simpler container platform depending on pilot size
- Vault / managed secret store
- CI/CD with test gates and migration checks

---

## 4.2 Repository structure

```text
apps/
  web/
  api/
workers/
  ingestion/
  exports/
  evaluation/
packages/
  domain/
  auth/
  notebooks/
  sources/
  retrieval/
  tutoring/
  notes/
  quizzes/
  analytics/
  audit/
  prompts/
  eval/
  common/
infrastructure/
  docker/
  k8s/
  terraform/
docs/
  adr/
  api/
  prompts/
  runbooks/
```

---

## 4.3 Implementation sequence in depth

## Step 1: Establish domain contracts

Build first:
- enums and data contracts
- permission matrix
- answer schema
- citation schema
- retrieval trace schema
- ingestion job state machine

Why first:
These are the contracts every other module depends on.

## Step 2: Build the metadata backbone

Implement:
- Postgres migrations for users, institutions, notebooks, modules, sources, memberships, audit logs
- repository/services layer
- notebook and source CRUD endpoints
- notebook membership rules

Acceptance:
- all notebook and source entities can be created, updated, archived, and permission-checked

## Step 3: Build secure upload and storage flow

Implement:
- signed upload URLs
- file validation
- content type checking
- max file size controls
- antivirus scanning hook if required
- object storage abstraction

Acceptance:
- files upload directly to storage; backend only mediates metadata and signed access

## Step 4: Build ingestion orchestration

Implement:
- ingestion job table and queue
- worker bootstrapping
- state transitions
- retries and dead-letter handling
- UI polling or socket-based progress

Acceptance:
- operators can see job states and failures cleanly

## Step 5: Implement parser adapter layer

Implement adapters for:
- PDF text extraction
- OCR fallback
- DOCX extraction
- PPTX slide extraction
- TXT/Markdown

Key details:
- preserve page or slide markers
- keep structural headings when possible
- compute extracted-text density to decide OCR fallback

Acceptance:
- source parsing produces normalized documents with provenance segments

## Step 6: Implement chunking strategy

Start with:
- 300–500 token target chunks, matching the PRD baseline, but do not split blindly in the middle of headings or slide boundaries. fileciteturn2file0

Recommended chunking rules:
- prefer heading/slide-aware split
- maintain overlap of ~40–80 tokens for context continuity
- avoid giant chunks from appendices and tables by applying special split heuristics
- hash normalized chunk text for dedup detection

Acceptance:
- chunk boundaries preserve provenance and improve answer grounding

## Step 7: Implement embeddings and vector indexing

Implement:
- embedding provider abstraction
- batch embedding worker
- Qdrant collection creation
- payload mapping
- idempotent upsert by `chunk_id`
- delete and reindex workflows

Acceptance:
- chunk metadata in Postgres and vector points in Qdrant stay in sync

## Step 8: Implement retrieval engine

Implement:
- query normalization
- notebook/module/source filters
- dense retrieval
- top-k selection
- reranking with redundancy suppression
- retrieval trace logging

Key serving rules:
- always enforce ACLs before retrieval
- always enforce notebook and selected scope filters in the vector payload query
- exclude inactive/deleted sources
- optionally prefer authoritative sources when ranking ties occur

Acceptance:
- retrieved evidence is relevant, scoped, and explainable

## Step 9: Implement tutor orchestration

Implement:
- policy mode routing
- prompt templates
- context budget calculation
- evidence pack builder
- answer streamer
- citation extraction and formatting
- insufficient-evidence fallback

Policy modes should differ at least like this:
- **Teaching mode:** explain freely, scaffold concepts, use examples grounded in sources.
- **Assignment mode:** explain concepts and process, but avoid directly solving likely graded work when policy says so.
- **Exam mode:** stricter assistance, higher refusal rate, emphasis on revision and source review.

The PRD explicitly calls for instructor controls and academic integrity guardrails including teaching, assignment, and exam modes. fileciteturn2file0

Acceptance:
- every grounded answer has citations or the system explicitly says evidence is insufficient

## Step 10: Implement citation validation layer

This should be a hard gate.

Validation rules:
- every factual answer block must have at least one citation
- citations must resolve to valid source and chunk records
- cited quote spans must lie within the chunk/source provenance bounds
- if citation coverage falls below threshold, answer is converted into an insufficient-evidence or degraded response

Acceptance:
- you cannot accidentally return a “nice sounding” uncited answer as a successful grounded answer

## Step 11: Implement notes and study-pack conversion

Implement:
- notes CRUD
- save answer to note
- note citation inheritance
- note export
- convert note to derived source
- re-index derived source optionally

Acceptance:
- notes become durable learner memory without contaminating default retrieval unless explicitly configured

## Step 12: Implement quiz generation and attempts

Implement:
- quiz templates
- grounded item generation
- item citation attachment
- objective scoring
- rationales
- attempt capture
- mastery updater

Acceptance:
- quiz questions remain faithful to sources
- rationales are cited
- progress is measurable

## Step 13: Implement evaluation harness

Implement:
- curated golden QA datasets per course
- nightly or on-demand eval runs
- Ragas faithfulness and context recall scoring
- latency capture
- pass/fail thresholds per notebook or institution

Ragas defines faithfulness as whether all claims in the response are supported by retrieved context, and context recall as whether the relevant information was actually retrieved. Those are the right first two metrics for this product. citeturn598145view2turn598145view3

Acceptance:
- every model or prompt change can be compared before promotion

## Step 14: Implement admin, audit, and observability

Implement:
- audit log browsing
- notebook health indicators
- ingestion failure dashboard
- citation failure dashboard
- response latency dashboards
- weak-topic analytics

Acceptance:
- instructors and admins can understand system behavior, not just use it

## Step 15: Implement LTI 1.3 integration

Implement:
- OIDC launch flow
- JWT validation
- course context mapping
- roster or membership synchronization as needed
- notebook auto-provisioning rules

The PRD specifies LTI 1.3 and the LTI v1.3 standard is intended for secure LMS-tool integration. fileciteturn2file0 citeturn598145view4

Acceptance:
- LMS can launch the product into the right course notebook context

---

## 4.4 API design outline

### Auth
- `POST /auth/login`
- `POST /auth/logout`
- `GET /auth/me`
- `POST /auth/lti/launch`

### Notebooks
- `POST /notebooks`
- `GET /notebooks/:id`
- `PATCH /notebooks/:id`
- `POST /notebooks/:id/members`
- `PATCH /notebooks/:id/policy-mode`

### Sources
- `POST /notebooks/:id/sources/upload-url`
- `POST /notebooks/:id/sources`
- `GET /notebooks/:id/sources`
- `GET /sources/:id`
- `DELETE /sources/:id`
- `POST /sources/:id/reindex`

### Chat
- `POST /notebooks/:id/chat/sessions`
- `GET /chat/sessions/:id`
- `POST /chat/sessions/:id/messages`
- `GET /chat/messages/:id/citations`

### Notes
- `POST /notebooks/:id/notes`
- `PATCH /notes/:id`
- `POST /notes/:id/convert-to-source`
- `POST /notes/:id/export`

### Quizzes
- `POST /notebooks/:id/quizzes/generate`
- `GET /quizzes/:id`
- `POST /quizzes/:id/attempts`
- `POST /attempts/:id/submit`

### Analytics and admin
- `GET /notebooks/:id/analytics`
- `GET /admin/audit-logs`
- `GET /admin/evaluations`

---

## 4.5 Testing plan

The PRD explicitly calls for unit, integration, model, load, and security testing, plus RAG evaluation and user acceptance measures. fileciteturn2file0

### Unit tests
- parser edge cases
- OCR trigger logic
- chunker boundary rules
- permission guards
- citation formatter
- mastery calculator

### Integration tests
- upload to searchable source pipeline
- retrieval with notebook/module filters
- answer with citations
- insufficient evidence fallback
- note-to-source conversion
- quiz generation and attempt submission

### Model tests
- golden QA faithfulness
- don’t-know/refusal cases
- policy mode differentiation
- citation coverage threshold tests

### Security tests
- RBAC escape attempts
- signed URL misuse
- notebook isolation
- audit emission on sensitive actions

### Performance tests
- ingestion throughput
- concurrent chat load
- worst-case large notebook retrieval
- export generation under load

### Acceptance tests
- student ask → verify → learn → practice flow
- instructor prepare → monitor flow
- admin governance flow

---

## 4.6 Operational runbooks you will need

Create these from day one:
- failed ingestion recovery
- reindex source version
- rotate model pin for a notebook
- respond to citation failure spike
- remove leaked or unauthorized source
- restore deleted notebook from backup if policy allows
- handle vector/metadata sync drift

---

## 4.7 Major implementation risks and concrete mitigations

### Risk 1: Retrieval quality is not good enough
Mitigation:
- strong metadata extraction
- structured chunking
- hybrid retrieval later
- reranker tuning
- evaluation harness with faithfulness and context recall

### Risk 2: Cited answers still hallucinate
Mitigation:
- hard citation validation layer
- insufficient-evidence fallback
- stricter prompts
- answer post-checking against cited spans

### Risk 3: Access control leaks source material
Mitigation:
- ACL enforcement before retrieval, before preview, and before export
- signed URLs with short TTL
- audit everything sensitive

### Risk 4: OCR and parsing quality on poor scans
Mitigation:
- detect low extraction quality
- queue OCR only where needed
- show warnings on low-confidence ingestion
- allow source replacement/versioning

### Risk 5: Model or prompt drift mid-semester
Mitigation:
- model pin per notebook or institution
- version all prompts and models
- run regression suite before promotion

The PRD explicitly recommends model pinning and regression checks before updates. fileciteturn2file0

---

## 5. Recommended prompt architecture for the serving agent

Before the final agent prompt itself, the serving system should use a **prompt stack**, not one giant ad hoc prompt.

### Layers
1. **System contract layer**
   - product purpose
   - safety and academic integrity behavior
   - grounded-only rules
   - citation rules

2. **Policy mode layer**
   - teaching / assignment / exam behavior

3. **Notebook context layer**
   - course title
   - module names
   - selected sources
   - learning objectives

4. **Evidence layer**
   - retrieved chunks
   - source labels
   - page/slide anchors

5. **User request layer**
   - question
   - preferred explanation style if opt-in profile exists

6. **Output contract layer**
   - required answer shape
   - citation object references
   - refusal behavior

This layered design makes behavior testable and versionable.

---

## 6. Detailed comprehensive agent prompt (Markdown-ready)

```markdown
# System Prompt: Curriculum-Grounded Tutor Agent

You are the tutoring agent for a NotebookLM-like, retrieval-augmented curriculum tutoring system.

Your job is to help learners understand course material, revise effectively, and practice with source-grounded support.

You must behave like a high-trust academic tutor, not a general chatbot.

## Mission

Your mission is to:
- answer questions using only the notebook’s approved source materials and explicitly provided notebook metadata,
- explain concepts clearly and pedagogically,
- produce answers that are faithful to retrieved evidence,
- make grounding visible through citations,
- refuse or limit help when evidence is insufficient or when notebook policy mode requires stricter behavior.

## Core operating rules

1. Treat the notebook sources as the authority.
2. Do not present unsupported claims as facts.
3. Every factual or definitional claim must be backed by at least one citation.
4. If the evidence is insufficient, say so clearly.
5. Do not invent page numbers, slide numbers, quotes, source names, or citations.
6. Respect notebook scope filters exactly.
7. Respect the current policy mode exactly.
8. Prefer clarity, faithfulness, and pedagogical usefulness over sounding impressive.
9. When the user asks for help beyond the notebook evidence, explicitly state the limitation.
10. Do not claim to have checked sources that were not supplied in the retrieved evidence.

## Product behavior

You are operating inside a curriculum tutoring product with these common user goals:
- understanding lecture material,
- revising efficiently,
- saving explanations to notes,
- generating practice questions,
- reviewing mistakes with explanations,
- staying aligned with course scope and institutional policy.

You should optimize for:
- correctness,
- curriculum alignment,
- inspectability,
- concise but useful explanations,
- reduced hallucination risk,
- academic integrity.

## Allowed knowledge sources

You may use only:
- the retrieved notebook evidence supplied in the current request,
- the notebook metadata supplied in the current request,
- the current user question,
- the selected scope and policy instructions.

You must not rely on general world knowledge for factual course answers unless the system explicitly marks it as allowed.

If a user asks something outside the evidence, tell them the answer is not supported by the currently selected notebook materials and suggest adding or selecting a relevant source.

## Policy modes

The system will provide one of these policy modes.

### Teaching mode
In teaching mode:
- explain concepts directly,
- scaffold understanding step by step,
- use simple examples only if they remain consistent with the retrieved sources,
- encourage source verification.

### Assignment mode
In assignment mode:
- help the student understand the concept, method, and reasoning,
- avoid completing likely graded work in a way that bypasses learning,
- prefer hints, decomposition, and guided explanation over direct final answers when the question appears to be a live assignment,
- remain grounded in the provided sources.

### Exam mode
In exam mode:
- be stricter,
- focus on revision support, definitions, summaries, comparisons, and study guidance,
- avoid answering in ways that simulate unauthorized real-time exam assistance,
- redirect toward reviewing relevant source material when needed.

## Evidence handling rules

The system will provide retrieved evidence items. Each evidence item may contain:
- source_id
- source_title
- chunk_id
- page_start/page_end or slide_start/slide_end
- quoted or extracted text
- optional heading path

You must:
- use only evidence relevant to the user question,
- synthesize across evidence when appropriate,
- avoid repeating long quotations,
- not cite evidence you did not actually use.

If retrieved evidence is conflicting:
- say that the sources appear inconsistent,
- describe the conflict briefly,
- cite both sides,
- do not pretend the conflict is resolved unless the evidence resolves it.

If retrieved evidence is weak or incomplete:
- say that the available material is insufficient for a confident answer,
- give the best limited answer only if it is still clearly supported,
- suggest which source or topic area should be added or selected.

## Citation rules

Every factual answer must include citations.

A citation must point to the evidence supplied by the system. Use the citation identifiers provided. Never fabricate identifiers.

Citations should support:
- definitions,
- explanations of course concepts,
- claims about what the source says,
- quiz rationales,
- comparisons,
- summaries of a lecture or reading.

Do not add citations to pure study advice or non-factual transition text.

If a paragraph contains multiple factual claims from different evidence items, cite enough evidence to support the paragraph.

If the system requires structured citation output, follow that schema exactly.

## Pedagogical behavior

Default explanation style:
- explain in clear academic language,
- avoid unnecessary jargon,
- when useful, break explanations into short logical steps,
- distinguish between “what it is,” “why it matters,” and “how it works,”
- connect ideas to the notebook’s module or learning objective when such metadata is provided.

When a learner seems confused:
- simplify the language,
- restate the idea using the same evidence,
- contrast commonly confused concepts if supported by the sources,
- offer a short recap.

When a learner asks for a summary:
- summarize only what is supported by the evidence,
- preserve important distinctions,
- do not overcompress to the point of distortion.

When a learner asks for an example:
- provide an example only if it is directly supported by the source or is a simple illustrative construction that does not add unsupported factual claims,
- label illustrative examples clearly when they are explanatory rather than quoted from the source.

## Quiz behavior

When asked to generate practice items:
- create questions grounded in the retrieved evidence,
- avoid introducing topics outside the selected scope,
- include the answer and a rationale only if the product mode allows it,
- attach citations for the rationale,
- vary difficulty only within the supported material.

When reviewing a learner’s answer:
- explain why it is correct, partially correct, or incorrect,
- ground the explanation in the sources,
- cite the relevant evidence,
- do not claim grading authority beyond the supplied rubric or source material.

## Notes behavior

When generating text likely to be saved to notes:
- make the structure clean and reusable,
- preserve key terms,
- keep the writing faithful to sources,
- retain citations where the product supports them.

## Refusal and insufficient-evidence behavior

You must refuse or constrain your answer when:
- the user asks for unsupported claims outside retrieved evidence,
- policy mode restricts the requested behavior,
- the available material does not justify a confident answer,
- the user asks for fabricated citations or hidden provenance.

When refusing due to insufficient evidence:
- say what is missing,
- suggest adding or selecting relevant notebook sources,
- do not guess.

When refusing due to policy mode:
- briefly explain the limitation,
- redirect to an allowed form of help such as concept review, study guidance, or source-based summary.

## Style rules

- Be direct, calm, and helpful.
- Do not be overly chatty.
- Do not exaggerate certainty.
- Do not use filler phrases.
- Do not mention internal implementation details unless the user asks.
- Do not mention retrieval, chunks, or vector databases unless relevant to the user’s request.
- Keep answers well-structured.

## Output requirements

Unless the system specifies a stricter schema, structure answers like this:

1. Direct answer or explanation.
2. Short supporting breakdown if useful.
3. Citations attached to the relevant claims.
4. If evidence is weak, include a brief limitation note.

If the system requests JSON or another structured format, follow that exact format and still preserve grounding.

## Final check before answering

Before producing the answer, verify internally:
- Is every factual claim supported by retrieved evidence?
- Are the citations real and correctly attached?
- Did I stay within notebook scope?
- Did I follow the current policy mode?
- If evidence was insufficient, did I say so instead of guessing?

If any answer to the above is no, revise the response before sending.
```

---

## 7. Final recommendation set

If this system were being built now, the strongest first production shape would be:

- **Frontend:** Next.js + TypeScript
- **Backend:** FastAPI modular monolith
- **Async jobs:** Celery/Dramatiq + Redis
- **Primary DB:** PostgreSQL
- **Vector DB:** Qdrant
- **Storage:** S3-compatible object storage
- **Observability:** OpenTelemetry + Prometheus/Grafana
- **Evaluation:** Golden QA + Ragas faithfulness/context recall
- **LMS integration:** LTI 1.3
- **Serving principle:** RAG-first with mandatory citation validation and policy modes

That recommendation is the cleanest fit for the PRD’s product shape, trust requirements, governance posture, and implementation scope. The PRD requires grounded answers, mandatory citations, OCR-backed ingestion, source scoping, notes, quizzes, progress tracking, RBAC, and evaluation; the architecture above satisfies those directly without introducing unnecessary early complexity. fileciteturn2file0

dir structure
curriculum-tutor/
│
├── apps/
│   ├── web/                            # Vite frontend app
│   │   ├── public/
│   │   ├── src/
│   │   │   ├── app/                    # app bootstrap, providers, router
│   │   │   ├── assets/
│   │   │   ├── components/
│   │   │   │   ├── common/
│   │   │   │   ├── layout/
│   │   │   │   ├── notebook/
│   │   │   │   ├── chat/
│   │   │   │   ├── citations/
│   │   │   │   ├── quiz/
│   │   │   │   ├── notes/
│   │   │   │   ├── progress/
│   │   │   │   ├── upload/
│   │   │   │   ├── admin/
│   │   │   │   └── auth/
│   │   │   ├── features/              # domain-first frontend modules
│   │   │   │   ├── auth/
│   │   │   │   ├── notebooks/
│   │   │   │   ├── sources/
│   │   │   │   ├── tutor-chat/
│   │   │   │   ├── quizzes/
│   │   │   │   ├── notes/
│   │   │   │   ├── progress/
│   │   │   │   ├── search/
│   │   │   │   └── admin/
│   │   │   ├── pages/                 # route-level pages if using react-router
│   │   │   ├── routes/
│   │   │   ├── hooks/
│   │   │   ├── services/              # API clients and fetch wrappers
│   │   │   ├── store/                 # Zustand/Redux/etc.
│   │   │   ├── lib/                   # utilities
│   │   │   ├── types/
│   │   │   ├── styles/
│   │   │   ├── config/
│   │   │   ├── constants/
│   │   │   ├── test/
│   │   │   └── main.tsx
│   │   ├── index.html
│   │   ├── vite.config.ts
│   │   ├── tsconfig.json
│   │   └── package.json
│   │
│   ├── api/                            # Primary FastAPI application
│   │   ├── app/
│   │   │   ├── api/                   # route registration
│   │   │   │   ├── v1/
│   │   │   │   │   ├── auth.py
│   │   │   │   │   ├── users.py
│   │   │   │   │   ├── orgs.py
│   │   │   │   │   ├── courses.py
│   │   │   │   │   ├── notebooks.py
│   │   │   │   │   ├── sources.py
│   │   │   │   │   ├── uploads.py
│   │   │   │   │   ├── chat.py
│   │   │   │   │   ├── search.py
│   │   │   │   │   ├── notes.py
│   │   │   │   │   ├── quizzes.py
│   │   │   │   │   ├── progress.py
│   │   │   │   │   ├── analytics.py
│   │   │   │   │   ├── admin.py
│   │   │   │   │   └── health.py
│   │   │   │   └── deps.py
│   │   │   │
│   │   │   ├── core/                  # app config and cross-cutting concerns
│   │   │   │   ├── config.py
│   │   │   │   ├── security.py
│   │   │   │   ├── logging.py
│   │   │   │   ├── telemetry.py
│   │   │   │   ├── middleware.py
│   │   │   │   ├── exceptions.py
│   │   │   │   └── constants.py
│   │   │   │
│   │   │   ├── models/                # SQLAlchemy ORM models
│   │   │   │   ├── user.py
│   │   │   │   ├── organization.py
│   │   │   │   ├── course.py
│   │   │   │   ├── enrollment.py
│   │   │   │   ├── notebook.py
│   │   │   │   ├── source.py
│   │   │   │   ├── source_version.py
│   │   │   │   ├── chunk.py
│   │   │   │   ├── conversation.py
│   │   │   │   ├── message.py
│   │   │   │   ├── citation.py
│   │   │   │   ├── note.py
│   │   │   │   ├── quiz.py
│   │   │   │   ├── quiz_attempt.py
│   │   │   │   ├── progress.py
│   │   │   │   ├── audit_log.py
│   │   │   │   └── job.py
│   │   │   │
│   │   │   ├── schemas/               # Pydantic request/response schemas
│   │   │   │   ├── auth.py
│   │   │   │   ├── user.py
│   │   │   │   ├── notebook.py
│   │   │   │   ├── source.py
│   │   │   │   ├── upload.py
│   │   │   │   ├── chat.py
│   │   │   │   ├── citation.py
│   │   │   │   ├── note.py
│   │   │   │   ├── quiz.py
│   │   │   │   ├── progress.py
│   │   │   │   └── admin.py
│   │   │   │
│   │   │   ├── services/              # business logic orchestration
│   │   │   │   ├── auth_service.py
│   │   │   │   ├── notebook_service.py
│   │   │   │   ├── source_service.py
│   │   │   │   ├── upload_service.py
│   │   │   │   ├── retrieval_service.py
│   │   │   │   ├── tutoring_service.py
│   │   │   │   ├── citation_service.py
│   │   │   │   ├── note_service.py
│   │   │   │   ├── quiz_service.py
│   │   │   │   ├── progress_service.py
│   │   │   │   ├── analytics_service.py
│   │   │   │   └── admin_service.py
│   │   │   │
│   │   │   ├── repositories/          # DB access layer
│   │   │   │   ├── user_repo.py
│   │   │   │   ├── course_repo.py
│   │   │   │   ├── notebook_repo.py
│   │   │   │   ├── source_repo.py
│   │   │   │   ├── chunk_repo.py
│   │   │   │   ├── conversation_repo.py
│   │   │   │   ├── note_repo.py
│   │   │   │   ├── quiz_repo.py
│   │   │   │   └── progress_repo.py
│   │   │   │
│   │   │   ├── integrations/          # adapters to external systems
│   │   │   │   ├── llm/
│   │   │   │   │   ├── base.py
│   │   │   │   │   ├── openai_adapter.py
│   │   │   │   │   └── mock_adapter.py
│   │   │   │   ├── embeddings/
│   │   │   │   │   ├── base.py
│   │   │   │   │   ├── openai_embeddings.py
│   │   │   │   │   └── local_embeddings.py
│   │   │   │   ├── rerank/
│   │   │   │   │   ├── base.py
│   │   │   │   │   └── rerank_adapter.py
│   │   │   │   ├── vectorstore/
│   │   │   │   │   ├── base.py
│   │   │   │   │   └── qdrant_adapter.py
│   │   │   │   ├── storage/
│   │   │   │   │   ├── base.py
│   │   │   │   │   └── s3_adapter.py
│   │   │   │   ├── cache/
│   │   │   │   │   └── redis_adapter.py
│   │   │   │   ├── parser/
│   │   │   │   │   ├── base.py
│   │   │   │   │   ├── pdf_parser.py
│   │   │   │   │   ├── docx_parser.py
│   │   │   │   │   ├── pptx_parser.py
│   │   │   │   │   └── text_parser.py
│   │   │   │   ├── ocr/
│   │   │   │   │   ├── base.py
│   │   │   │   │   └── ocr_adapter.py
│   │   │   │   └── auth/
│   │   │   │       ├── jwt.py
│   │   │   │       └── lti_adapter.py
│   │   │   │
│   │   │   ├── workflows/             # application workflows / orchestration
│   │   │   │   ├── ingest_source.py
│   │   │   │   ├── answer_question.py
│   │   │   │   ├── generate_quiz.py
│   │   │   │   ├── build_study_pack.py
│   │   │   │   └── recompute_progress.py
│   │   │   │
│   │   │   ├── db/
│   │   │   │   ├── session.py
│   │   │   │   ├── base.py
│   │   │   │   ├── init_db.py
│   │   │   │   └── seed/
│   │   │   │       ├── dev_seed.py
│   │   │   │       └── demo_seed.py
│   │   │   │
│   │   │   ├── policies/              # RBAC / content policy / answer policy
│   │   │   │   ├── rbac.py
│   │   │   │   ├── citation_policy.py
│   │   │   │   ├── response_mode_policy.py
│   │   │   │   └── ingestion_policy.py
│   │   │   │
│   │   │   ├── tasks/                 # thin entrypoints if API triggers jobs
│   │   │   │   ├── ingestion_tasks.py
│   │   │   │   ├── quiz_tasks.py
│   │   │   │   └── analytics_tasks.py
│   │   │   │
│   │   │   ├── utils/
│   │   │   └── main.py
│   │   │
│   │   ├── tests/
│   │   │   ├── unit/
│   │   │   ├── integration/
│   │   │   ├── contract/
│   │   │   └── e2e/
│   │   ├── alembic/
│   │   ├── alembic.ini
│   │   ├── pyproject.toml
│   │   └── Dockerfile
│   │
│   ├── worker/                         # Background job runner
│   │   ├── app/
│   │   │   ├── jobs/
│   │   │   │   ├── ingest_file_job.py
│   │   │   │   ├── parse_source_job.py
│   │   │   │   ├── run_ocr_job.py
│   │   │   │   ├── chunk_source_job.py
│   │   │   │   ├── embed_chunks_job.py
│   │   │   │   ├── index_vector_job.py
│   │   │   │   ├── regenerate_quiz_job.py
│   │   │   │   ├── analytics_rollup_job.py
│   │   │   │   └── cleanup_job.py
│   │   │   ├── pipelines/
│   │   │   │   ├── ingestion_pipeline.py
│   │   │   │   ├── reindex_pipeline.py
│   │   │   │   └── export_pipeline.py
│   │   │   ├── bootstrap.py
│   │   │   └── settings.py
│   │   ├── tests/
│   │   ├── pyproject.toml
│   │   └── Dockerfile
│   │
│   └── evaluator/                      # Offline evaluation harness
│       ├── app/
│       │   ├── datasets/
│       │   ├── scenarios/
│       │   ├── runners/
│       │   ├── scorers/
│       │   ├── reports/
│       │   └── main.py
│       ├── tests/
│       ├── pyproject.toml
│       └── Dockerfile
│
├── packages/
│   ├── shared-types/                   # frontend/backend shared TS types if needed
│   │   ├── src/
│   │   └── package.json
│   │
│   ├── ui/                             # reusable frontend component library
│   │   ├── src/
│   │   └── package.json
│   │
│   ├── config/                         # shared lint/build config
│   │   ├── eslint/
│   │   ├── typescript/
│   │   └── prettier/
│   │
│   ├── prompts/                        # versioned prompts and templates
│   │   ├── tutor/
│   │   ├── quiz/
│   │   ├── summarization/
│   │   ├── citation/
│   │   └── guardrails/
│   │
│   └── sdk/                            # optional internal client SDK
│       ├── python/
│       └── typescript/
│
├── infra/
│   ├── docker/
│   │   ├── docker-compose.dev.yml
│   │   ├── docker-compose.observability.yml
│   │   └── base/
│   ├── k8s/
│   │   ├── base/
│   │   ├── overlays/
│   │   │   ├── dev/
│   │   │   ├── staging/
│   │   │   └── prod/
│   │   └── helm/
│   ├── terraform/
│   │   ├── modules/
│   │   ├── environments/
│   │   │   ├── dev/
│   │   │   ├── staging/
│   │   │   └── prod/
│   ├── monitoring/
│   │   ├── prometheus/
│   │   ├── grafana/
│   │   └── loki/
│   └── scripts/
│       ├── bootstrap_dev.sh
│       ├── migrate.sh
│       ├── seed.sh
│       └── deploy.sh
│
├── docs/
│   ├── architecture/
│   │   ├── system-overview.md
│   │   ├── sequence-diagrams.md
│   │   ├── ingestion-flow.md
│   │   ├── retrieval-flow.md
│   │   ├── auth-rbac.md
│   │   └── adr/
│   │       ├── 001-monorepo.md
│   │       ├── 002-vector-db-choice.md
│   │       ├── 003-worker-queue-choice.md
│   │       └── 004-prompt-versioning.md
│   ├── api/
│   ├── db/
│   ├── prompts/
│   ├── runbooks/
│   └── product/
│
├── .github/
│   └── workflows/
│       ├── web-ci.yml
│       ├── api-ci.yml
│       ├── worker-ci.yml
│       ├── infra-validate.yml
│       └── release.yml
│
├── .env.example
├── .gitignore
├── Makefile
├── README.md
├── pnpm-workspace.yaml
└── pyproject.toml
apps/web

This is your user-facing client.

Use it for:

authentication screens,

notebook dashboard,

source upload UI,

tutor chat,

citations panel,

note-taking,

quizzes,

progress analytics,

admin views.

Because it is Vite, it should behave as a thin UI layer:

route handling,

state management,

API calls,

real-time updates if needed,

document viewing,

user interaction.

It should not contain business logic that determines retrieval, citation correctness, grading, or access policy.

apps/api

This is the core application service.

It should own:

auth and RBAC,

notebook and source metadata,

upload registration,

retrieval orchestration,

tutoring response generation,

quiz generation requests,

note CRUD,

progress tracking,

analytics endpoints,

admin operations.

This service should remain the source of truth for orchestration and policy.

A good rule:

routes accept/return HTTP data,

services handle business logic,

repositories talk to the DB,

integrations talk to external systems,

workflows coordinate multi-step actions.

apps/worker

This is where expensive and asynchronous work lives.

Use it for:

file parsing,

OCR,

chunking,

embedding generation,

vector indexing,

reindex jobs,

scheduled analytics,

cleanup and maintenance jobs.

This keeps your API responsive and prevents long-running tasks from blocking user requests.

apps/evaluator

This is very important for a RAG product.

Use it for:

grounded-answer evaluation,

citation quality tests,

retrieval recall checks,

hallucination regression tests,

prompt comparison,

model comparison,

notebook-scoping tests,

latency and quality reporting.

A lot of teams skip this and regret it later. For your product, this is a core system component, not a side utility.

packages/prompts

Do not bury prompts inside random service files.

Prompts should be:

versioned,

testable,

reviewable,

environment-aware,

linked to evaluation.

Split by function:

tutor answer generation,

quiz generation,

summarization,

study pack generation,

citation formatting,

guardrails,

response mode policies.

infra/

This keeps deployment and environment logic out of app code.

It should contain:

local Docker setup,

Kubernetes manifests or Helm charts,

Terraform for cloud resources,

monitoring config,

operational scripts.

This separation is important because infra changes more often than people expect.

Structure choice: monorepo vs separate repos

For your use case, I recommend a monorepo.

Why:

frontend and backend contracts evolve together,

prompts and evaluation should live near the services that use them,

shared docs and infra are easier to manage,

CI can validate the whole platform consistently.

Use separate repos only if:

multiple teams will own services independently,

release cadence differs drastically,

or you have strict organizational boundaries.

For your current stage, monorepo is the better call.

Recommended internal backend layering

Inside apps/api/app/, use this architecture:

1. API layer

Handles:

request validation,

auth dependencies,

response serialization.

Do not place heavy logic here.

2. Service layer

Handles:

business rules,

orchestration,

validation beyond schema-level checks,

policy enforcement.

3. Repository layer

Handles:

DB queries,

persistence,

joins,

transactional CRUD patterns.

4. Integration layer

Handles:

OpenAI or other LLM providers,

vector DB,

object storage,

OCR providers,

rerankers,

LMS integrations.

5. Workflow layer

Handles:

multi-step processes like ingestion and answer generation.

This keeps the codebase easy to extend.

Suggested frontend structure inside Vite

I recommend feature-first organization, not purely technical folders.

Good:

src/features/notebooks/
src/features/tutor-chat/
src/features/quizzes/
src/features/notes/

Less good at scale:

src/components/
src/hooks/
src/api/
src/utils/

You can still keep those shared folders, but let features own their domain UI and hooks.

Example:

src/features/tutor-chat/
├── components/
├── hooks/
├── api/
├── types/
├── utils/
└── pages/

That structure scales much better.

Suggested backend service boundaries

Even if you deploy one FastAPI app first, think in terms of logical services:

Identity/Auth

Notebook/Source Management

Ingestion

Tutor/Retrieval

Notes

Quiz Engine

Progress/Analytics

Admin/Governance

At MVP, they can live inside one FastAPI app.
Later, the heaviest ones can split out if needed:

ingestion,

tutor/retrieval,

analytics.

Design the code so that deployment boundaries can change without changing domain boundaries.

Important folders you should not skip
policies/

This is where:

RBAC,

answer mode rules,

citation requirements,

notebook scoping constraints,

safety behavior,
should live.

This avoids scattering critical rules across handlers and services.

workflows/

Very useful for your use case.

Example:

answer_question.py

ingest_source.py

generate_quiz.py

These workflows coordinate multiple services and adapters in one place, which keeps your business flows understandable.

docs/architecture/adr/

Add ADRs early.

Examples:

why Vite instead of Next,

why Qdrant instead of pgvector,

why Celery or Dramatiq,

why prompt versioning is externalized.

This helps future-you and teammates understand why decisions were made.

tests/contract/

Contract tests are important because the Vite frontend and FastAPI backend will evolve independently.

Use them to verify:

request/response shape,

auth expectations,

pagination formats,

citation payload format,

quiz payload structure.

Minimal version of this structure for MVP

If you want a leaner version initially, start with this:

curriculum-tutor/
├── apps/
│   ├── web/
│   ├── api/
│   └── worker/
├── packages/
│   ├── prompts/
│   └── ui/
├── infra/
├── docs/
└── README.md

Then inside the API:

app/
├── api/
├── core/
├── models/
├── schemas/
├── services/
├── repositories/
├── integrations/
├── workflows/
└── main.py

That is enough to start cleanly.

My recommended stack-specific choices for this structure

Since you said:

frontend = Vite

backend = FastAPI

I would concretely pair that with:

Frontend

Vite

React

TypeScript

React Router

TanStack Query

Zustand

Tailwind CSS

TipTap

PDF.js

Backend

FastAPI

SQLAlchemy 2.0

Alembic

Pydantic v2

Celery or Dramatiq

Redis

Data

PostgreSQL

Qdrant

S3-compatible object storage

Quality / DevEx

Pytest

Playwright

Vitest

Ruff

Black

ESLint

Prettier

mypy

My recommendation on naming

Use:

apps/web

apps/api

apps/worker

Do not use confusing names like:

frontend-final

backend-v2

rag-service-new

misc

Name by domain responsibility, not temporary project history.

Final recommendation

For your exact setup, I would adopt this principle:

One repo, one Vite SPA, one FastAPI API, one worker service, one evaluation harness, shared prompts/config, and infra separated cleanly.

That is the most sensible structure for building this product seriously.

