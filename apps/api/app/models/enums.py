from __future__ import annotations

from enum import StrEnum


class AuthProvider(StrEnum):
    LOCAL = "local"
    CLERK = "clerk"
    OIDC = "oidc"
    SAML = "saml"
    LTI = "lti"


class UserStatus(StrEnum):
    ACTIVE = "active"
    INVITED = "invited"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class InstitutionRole(StrEnum):
    STUDENT = "student"
    TA = "ta"
    INSTRUCTOR = "instructor"
    ADMIN = "admin"


class NotebookVisibility(StrEnum):
    PRIVATE = "private"
    COURSE = "course"
    SHARED = "shared"
    INSTITUTION = "institution"


class PolicyMode(StrEnum):
    TEACHING = "teaching"
    ASSIGNMENT = "assignment"
    EXAM = "exam"


class NotebookMembershipRole(StrEnum):
    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"
    STUDENT = "student"
    TA = "ta"
    INSTRUCTOR = "instructor"


class ModuleType(StrEnum):
    WEEK = "week"
    TOPIC = "topic"
    UNIT = "unit"
    OBJECTIVE_GROUP = "objective_group"


class SourceType(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    TXT = "txt"
    MD = "md"
    URL = "url"
    AUDIO_TRANSCRIPT = "audio_transcript"
    DERIVED_NOTE = "derived_note"


class SourceStatus(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"
    ARCHIVED = "archived"
    DELETED = "deleted"


class SourceOrigin(StrEnum):
    UPLOAD = "upload"
    CONNECTOR = "connector"
    LTI = "lti"
    NOTE_CONVERSION = "note_conversion"


class SegmentType(StrEnum):
    PAGE = "page"
    SLIDE = "slide"
    SECTION = "section"
    TIME_RANGE = "time_range"


class IngestionJobType(StrEnum):
    PARSE = "parse"
    OCR = "ocr"
    NORMALIZE = "normalize"
    CHUNK = "chunk"
    EMBED = "embed"
    INDEX = "index"
    REINDEX = "reindex"
    EXPORT_CLEANUP = "export_cleanup"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChatRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MessageStatus(StrEnum):
    COMPLETE = "complete"
    STREAMING = "streaming"
    FAILED = "failed"
    REDACTED = "redacted"


class AnswerType(StrEnum):
    GROUNDED_ANSWER = "grounded_answer"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    POLICY_REFUSAL = "policy_refusal"
    QUIZ_EXPLANATION = "quiz_explanation"
    SUMMARY = "summary"


class RetrievalMode(StrEnum):
    DENSE = "dense"
    HYBRID = "hybrid"
    SPARSE = "sparse"
    MANUAL_SCOPE_ONLY = "manual_scope_only"


class NoteType(StrEnum):
    MANUAL = "manual"
    SAVED_ANSWER = "saved_answer"
    STUDY_PACK = "study_pack"
    FAQ = "faq"
    TEMPLATE = "template"


class NoteVisibility(StrEnum):
    PRIVATE = "private"
    SHARED_NOTEBOOK = "shared_notebook"
    INSTRUCTOR_ONLY = "instructor_only"


class QuizDifficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    MIXED = "mixed"


class QuizGenerationMode(StrEnum):
    TUTOR_GENERATED = "tutor_generated"
    INSTRUCTOR_AUTHORED = "instructor_authored"
    HYBRID = "hybrid"


class QuizStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class QuizItemType(StrEnum):
    MCQ = "mcq"
    SHORT_ANSWER = "short_answer"
    TRUE_FALSE = "true_false"


class AttemptStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    GRADED = "graded"
    ABANDONED = "abandoned"


class ModelProfileStatus(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"


class ModelOperationType(StrEnum):
    CHAT = "chat"
    QUIZ_GENERATION = "quiz_generation"
    RERANK = "rerank"
    EVALUATION = "evaluation"
    SUMMARY = "summary"


class EvaluationType(StrEnum):
    RAGAS = "ragas"
    LATENCY = "latency"
    SECURITY = "security"
    GOLDEN_QA = "golden_qa"
    AB_TEST = "ab_test"


class EvaluationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DeploymentMode(StrEnum):
    CLOUD = "cloud"
    HYBRID = "hybrid"
    ON_PREM = "on_prem"


class PrivacyMode(StrEnum):
    STANDARD = "standard"
    STRICT = "strict"


class CourseOfferingStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class SharingMode(StrEnum):
    PRIVATE = "private"
    SHARED = "shared"


class DeleteStrategy(StrEnum):
    SOFT_DELETE = "soft_delete"
    HARD_DELETE = "hard_delete"
    REDACT = "redact"
