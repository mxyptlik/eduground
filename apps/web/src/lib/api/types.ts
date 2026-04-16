export type Role = "student" | "ta" | "instructor" | "admin";
export type PolicyMode = "teaching" | "assignment" | "exam";
export type SourceStatus =
  | "uploaded"
  | "processing"
  | "indexed"
  | "failed"
  | "archived"
  | "deleted";
export type AnswerType =
  | "grounded_answer"
  | "insufficient_evidence"
  | "policy_refusal"
  | "quiz_explanation"
  | "summary";
export type QuizStatus = "draft" | "published" | "archived";

export type AuthUser = {
  id: string;
  email: string;
  display_name: string;
  institution_id: string | null;
  role: Role | null;
  external_subject_id?: string | null;
  active_organization_id?: string | null;
  active_organization_slug?: string | null;
  memberships?: {
    institution_id: string;
    institution_name: string;
    institution_slug: string;
    external_organization_id?: string | null;
    role: Role;
  }[];
};

export type Notebook = {
  id: string;
  institution_id: string;
  title: string;
  description: string | null;
  owner_user_id: string;
  visibility: "private" | "course" | "shared" | "institution";
  policy_mode: PolicyMode;
  course_offering_id: string | null;
  source_count: number;
  learner_count: number;
};

export type Source = {
  id: string;
  notebook_id: string;
  module_id: string | null;
  source_type: "pdf" | "docx" | "pptx" | "txt" | "md" | "url" | "audio_transcript" | "derived_note";
  title: string;
  storage_key: string;
  mime_type: string;
  checksum_sha256: string;
  byte_size: number;
  status: SourceStatus;
  version_number: number;
  latest_job?: IngestionJobSummary | null;
};

export type IngestionJob = {
  id: string;
  source_id: string;
  source_version_id?: string | null;
  job_type: "parse" | "ocr" | "normalize" | "chunk" | "embed" | "index" | "reindex" | "export_cleanup";
  status: "queued" | "running" | "retrying" | "completed" | "failed" | "cancelled";
  attempt_count: number;
  stage?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  progress_json?: Record<string, unknown> | null;
  segment_count: number;
  chunk_count: number;
  embedded_count: number;
  indexed_count: number;
  trace_id?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
};

export type IngestionJobSummary = {
  id: string;
  job_type: "parse" | "ocr" | "normalize" | "chunk" | "embed" | "index" | "reindex" | "export_cleanup";
  status: "queued" | "running" | "retrying" | "completed" | "failed" | "cancelled";
  stage?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  attempt_count: number;
  finished_at?: string | null;
};

export type UploadUrlResponse = {
  upload_intent_id: string;
  storage_key: string;
  upload_url: string;
  expires_in_seconds: number;
};

export type Citation = {
  id: string;
  source_id: string;
  chunk_id: string;
  source_segment_id?: string | null;
  display_label: string;
  page_start?: number | null;
  page_end?: number | null;
  slide_start?: number | null;
  slide_end?: number | null;
  quote_text: string;
};

export type RetrievalTraceItem = {
  chunk_id: string;
  source_id: string;
  initial_score: number;
  rank_before: number;
  rank_after?: number | null;
  was_used_in_context: boolean;
};

export type RetrievalTrace = {
  id: string;
  query_text: string;
  retrieval_mode: "dense" | "hybrid" | "sparse" | "manual_scope_only";
  filters_json: Record<string, unknown>;
  latency_ms: number;
  top_k_requested: number;
  top_k_used: number;
  reranker_used: boolean;
  items: RetrievalTraceItem[];
};

export type TutorAnswer = {
  answer_type: AnswerType;
  content_markdown: string;
  citations: Citation[];
  refusal_reason?: string | null;
  retrieval_trace?: RetrievalTrace | null;
};

export type TutorChatSourcePreview = {
  source_id: string;
  chunk_id: string;
  display_label: string;
  page_start?: number | null;
  page_end?: number | null;
  slide_start?: number | null;
  slide_end?: number | null;
  quote_text: string;
};

export type TutorChatStatusEvent = {
  phase: "retrieving" | "generating" | string;
  message: string;
};

export type TutorChatStreamEvent =
  | { type: "status"; data: TutorChatStatusEvent }
  | { type: "sources"; data: TutorChatSourcePreview[] }
  | { type: "content"; data: string }
  | { type: "final"; data: TutorAnswer }
  | { type: "error"; data: { message?: string; code?: string } | string };

export type Note = {
  id: string;
  notebook_id: string;
  author_user_id: string;
  title: string;
  content_markdown: string;
  note_type: "manual" | "saved_answer" | "study_pack" | "faq" | "template";
  converted_source_id?: string | null;
  visibility: "private" | "shared_notebook" | "instructor_only";
};

export type QuizItem = {
  id: string;
  item_type: "mcq" | "short_answer" | "true_false";
  prompt_text: string;
  options_json?: string[] | null;
  rationale_markdown: string;
  position: number;
};

export type Quiz = {
  id: string;
  notebook_id: string;
  title: string;
  difficulty: "easy" | "medium" | "hard" | "mixed";
  status: QuizStatus;
  created_at: string;
  items: QuizItem[];
};

export type QuizAttempt = {
  id: string;
  quiz_id: string;
  user_id: string;
  started_at: string;
  score_numeric?: number | null;
  submitted_at?: string | null;
  score_percent?: number | null;
  status: "in_progress" | "submitted" | "graded" | "abandoned";
  passed?: boolean | null;
  correct_count?: number | null;
  total_items?: number | null;
  result_comment?: string | null;
  results?: QuizAttemptResultItem[];
};

export type QuizAttemptResultItem = {
  quiz_item_id: string;
  submitted_answer_json: Record<string, unknown>;
  correct_answer_json: Record<string, unknown>;
  is_correct?: boolean | null;
  feedback_markdown?: string | null;
};

export type NotebookAnalytics = {
  notebook_id: string;
  source_count: number;
  indexed_source_count: number;
  chat_session_count: number;
  note_count: number;
  quiz_count: number;
};

export type AuditLog = {
  id: string;
  action_type: string;
  resource_type: string;
  resource_id: string;
  notebook_id?: string | null;
  metadata_json: Record<string, unknown>;
};

export type Evaluation = {
  id: string;
  notebook_id?: string | null;
  model_profile_id: string;
  evaluation_type: string;
  status: string;
};

export type ChatSession = {
  id: string;
  notebook_id: string;
  user_id: string;
  title?: string | null;
  policy_mode_snapshot: PolicyMode;
};

export type ApiErrorShape = {
  code: string;
  message: string;
  request_id?: string | null;
  retryable?: boolean;
  provider?: string | null;
  details?: unknown;
};
