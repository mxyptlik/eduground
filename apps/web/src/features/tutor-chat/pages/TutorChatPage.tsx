import { ChangeEvent, FormEvent, KeyboardEvent as ReactKeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { apiClient } from "@/lib/api/client";
import { getDisplayErrorMessage } from "@/lib/api/errors";
import type { Source, TutorAnswer, TutorChatSourcePreview, TutorChatStreamEvent } from "@/lib/api/types";
import { createStudyDraftFromTutor } from "@/features/notes/lib/noteDraft";
import { useAppSession } from "@/lib/session/AppSessionContext";

type TranscriptEntry =
  | { id: string; role: "user"; content: string }
  | {
      id: string;
      role: "assistant";
      answer: TutorAnswer;
      streaming?: boolean;
      sourcePreviews?: TutorChatSourcePreview[];
      statusMessage?: string;
      prompt?: string;
    };

type PersistedTutorChatState = {
  version: number;
  chatSessionId: string | null;
  question: string;
  transcript: TranscriptEntry[];
  selectedSourceIds: string[];
  savedAnswerIds: Record<string, boolean>;
};

const TUTOR_CHAT_STORAGE_VERSION = 1;

function getTutorChatStorageKey(notebookId: string) {
  return `eduground:tutor-chat:${notebookId}`;
}

function sanitizeTranscriptForPersistence(transcript: TranscriptEntry[]): TranscriptEntry[] {
  return transcript.map((entry) =>
    entry.role === "assistant"
      ? {
          ...entry,
          streaming: false,
          statusMessage: undefined,
        }
      : entry,
  );
}

function readPersistedTutorState(notebookId: string): PersistedTutorChatState | null {
  try {
    const raw = window.sessionStorage.getItem(getTutorChatStorageKey(notebookId));
    if (!raw) {
      return null;
    }

    const parsed = JSON.parse(raw) as PersistedTutorChatState;
    if (parsed.version !== TUTOR_CHAT_STORAGE_VERSION || !Array.isArray(parsed.transcript)) {
      return null;
    }

    return {
      version: parsed.version,
      chatSessionId: typeof parsed.chatSessionId === "string" ? parsed.chatSessionId : null,
      question: typeof parsed.question === "string" ? parsed.question : "",
      transcript: parsed.transcript,
      selectedSourceIds: Array.isArray(parsed.selectedSourceIds) ? parsed.selectedSourceIds : [],
      savedAnswerIds:
        parsed.savedAnswerIds && typeof parsed.savedAnswerIds === "object" ? parsed.savedAnswerIds : {},
    };
  } catch {
    return null;
  }
}

function isRetryingSource(source: Source) {
  return (
    source.latest_job?.job_type === "reindex" &&
    (source.latest_job?.status === "queued" ||
      source.latest_job?.status === "running" ||
      source.latest_job?.status === "retrying")
  );
}

function isProcessingSource(source: Source) {
  return (
    source.status === "processing" ||
    source.latest_job?.status === "queued" ||
    source.latest_job?.status === "running" ||
    source.latest_job?.status === "retrying"
  );
}

function getSourceActionLabel(source: Source) {
  if (source.status === "failed") {
    return "Retry";
  }
  if (isRetryingSource(source)) {
    return "Retrying...";
  }
  if (isProcessingSource(source)) {
    return "Processing...";
  }
  return "Reindex";
}

function TutorMarkdown({ content }: { content: string }) {
  return (
    <div className="workspace-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}

function PaperclipIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M7.8 7.1v6.2a2.9 2.9 0 1 0 5.8 0V6.6a4.1 4.1 0 0 0-8.2 0v7.2a5.3 5.3 0 1 0 10.6 0V8.7" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.7" />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M4 9.8l11-5.3-3.4 11-1.8-4.3L4 9.8Z" fill="none" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.7" />
    </svg>
  );
}

function inferSourceType(filename: string): Source["source_type"] {
  const extension = filename.split(".").pop()?.toLowerCase();
  switch (extension) {
    case "pdf":
      return "pdf";
    case "docx":
      return "docx";
    case "pptx":
      return "pptx";
    case "md":
      return "md";
    case "txt":
      return "txt";
    default:
      return "txt";
  }
}

function inferMimeType(file: File) {
  return file.type || "application/octet-stream";
}

async function hashFile(file: File) {
  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export function TutorChatPage() {
  const navigate = useNavigate();
  const { notebooks, selectedNotebookId } = useAppSession();
  const selectedNotebook = notebooks.find((notebook) => notebook.id === selectedNotebookId) ?? null;
  const [chatSessionId, setChatSessionId] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [streamedSources, setStreamedSources] = useState<TutorChatSourcePreview[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [sourcesLoading, setSourcesLoading] = useState(false);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [savedAnswerIds, setSavedAnswerIds] = useState<Record<string, boolean>>({});
  const [savingAnswerIds, setSavingAnswerIds] = useState<Record<string, boolean>>({});
  const [uploadingSource, setUploadingSource] = useState(false);
  const [uploadStatusMessage, setUploadStatusMessage] = useState<string | null>(null);
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [sourceScopeOpen, setSourceScopeOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const sourceScopeRef = useRef<HTMLDivElement | null>(null);
  const sourcesOverlayRef = useRef<HTMLDivElement | null>(null);
  const isChatIdle = transcript.length === 0;
  const chatSourcesOverlayOpen = false;
  const processingSources: Source[] = [];
  const failedSources: Source[] = [];
  const busySourceId: string | null = null;

  function setChatSourcesOverlayOpen(_open: boolean) {}

  async function handleSourceAction(_source: Source) {}

  async function handleDeleteSource(_sourceId: string) {}

  useEffect(() => {
    if (!selectedNotebookId) {
      setChatSessionId(null);
      setTranscript([]);
      setStreamedSources([]);
      setQuestion("");
      setError(null);
      setSavedAnswerIds({});
      setUploadStatusMessage(null);
      setSelectedSourceIds([]);
      setSourceScopeOpen(false);
      return;
    }

    const persistedState = readPersistedTutorState(selectedNotebookId);
    setChatSessionId(persistedState?.chatSessionId ?? null);
    setTranscript(persistedState?.transcript ?? []);
    setStreamedSources([]);
    setQuestion(persistedState?.question ?? "");
    setError(null);
    setSavedAnswerIds(persistedState?.savedAnswerIds ?? {});
    setUploadStatusMessage(null);
    setSelectedSourceIds(persistedState?.selectedSourceIds ?? []);
    setSourceScopeOpen(false);
  }, [selectedNotebookId]);

  useEffect(() => {
    if (!error) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      setError(null);
    }, 6000);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [error]);

  useEffect(() => {
    if (!selectedNotebookId) {
      return;
    }

    const payload: PersistedTutorChatState = {
      version: TUTOR_CHAT_STORAGE_VERSION,
      chatSessionId,
      question,
      transcript: sanitizeTranscriptForPersistence(transcript),
      selectedSourceIds,
      savedAnswerIds,
    };

    window.sessionStorage.setItem(getTutorChatStorageKey(selectedNotebookId), JSON.stringify(payload));
  }, [chatSessionId, question, savedAnswerIds, selectedNotebookId, selectedSourceIds, transcript]);

  useEffect(() => {
    if (!sourceScopeOpen) {
      return;
    }

    function handlePointerDown(event: MouseEvent) {
      const target = event.target as Node;

      if (!sourceScopeRef.current?.contains(target)) {
        setSourceScopeOpen(false);
      }
    }

    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        setSourceScopeOpen(false);
      }
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [sourceScopeOpen]);

  async function refreshSources(notebookId: string) {
    setSourcesLoading(true);
    setSourceError(null);
    try {
      const sourceList = await apiClient.listSources(notebookId);
      setSources(sourceList);
    } catch (loadError) {
      setSourceError(getDisplayErrorMessage(loadError, "Unable to load notebook sources."));
    } finally {
      setSourcesLoading(false);
    }
  }

  useEffect(() => {
    const notebookId = selectedNotebookId;

    if (!notebookId) {
      setSources([]);
      setSourcesLoading(false);
      setSourceError(null);
      return;
    }

    void refreshSources(notebookId);
  }, [selectedNotebookId]);

  const latestAssistantEntry =
    [...transcript]
      .reverse()
      .find(
        (entry): entry is Extract<TranscriptEntry, { role: "assistant" }> => entry.role === "assistant",
      ) ?? null;

  const activeSources = useMemo(
    () => sources.filter((source) => source.status !== "deleted" && source.status !== "archived"),
    [sources],
  );
  const indexedSourceCount = useMemo(
    () => activeSources.filter((source) => source.status === "indexed").length,
    [activeSources],
  );
  const indexedSources = useMemo(
    () => activeSources.filter((source) => source.status === "indexed"),
    [activeSources],
  );
  const sourceScopeLabel =
    selectedSourceIds.length === 0
      ? indexedSourceCount === 1
        ? "1 source"
        : `${indexedSourceCount} sources`
      : `${selectedSourceIds.length} selected`;

  useEffect(() => {
    setSelectedSourceIds((current) => current.filter((sourceId) => indexedSources.some((source) => source.id === sourceId)));
  }, [indexedSources]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!selectedNotebookId || !question.trim()) {
      return;
    }

    setSubmitting(true);
    setError(null);
    const trimmedQuestion = question.trim();

    try {
      let activeChatSessionId = chatSessionId;
      if (!activeChatSessionId) {
        const session = await apiClient.createChatSession(
          selectedNotebookId,
          `${selectedNotebook?.title ?? "Notebook"} tutoring`,
        );
        activeChatSessionId = session.id;
        setChatSessionId(session.id);
      }

      setTranscript((current) => [
        ...current,
        { id: `user-${Date.now()}`, role: "user", content: trimmedQuestion },
      ]);
      setQuestion("");
      const assistantId = `assistant-${Date.now()}`;
      setStreamedSources([]);
      setTranscript((current) => [
        ...current,
        {
          id: assistantId,
          role: "assistant",
          streaming: true,
          sourcePreviews: [],
          statusMessage: "Retrieving sources and notebook notes...",
          prompt: trimmedQuestion,
          answer: {
            answer_type: "grounded_answer",
            content_markdown: "",
            citations: [],
            refusal_reason: null,
            retrieval_trace: null,
          },
        },
      ]);

      const events = await apiClient.streamChatMessage(activeChatSessionId, {
        content_markdown: trimmedQuestion,
        selected_source_ids: selectedSourceIds,
      }, (event) => {
        if (event.type === "status") {
          setTranscript((current) =>
            current.map((entry) =>
              entry.id === assistantId && entry.role === "assistant"
                ? { ...entry, statusMessage: event.data.message }
                : entry,
            ),
          );
          return;
        }
        if (event.type === "sources") {
          setStreamedSources(event.data);
          setTranscript((current) =>
            current.map((entry) =>
              entry.id === assistantId && entry.role === "assistant"
                ? { ...entry, sourcePreviews: event.data }
                : entry,
            ),
          );
          return;
        }
        if (event.type === "content") {
          setTranscript((current) =>
            current.map((entry) =>
              entry.id === assistantId && entry.role === "assistant"
                ? {
                    ...entry,
                    answer: { ...entry.answer, content_markdown: `${entry.answer.content_markdown}${event.data}` },
                  }
                : entry,
            ),
          );
          return;
        }
        if (event.type === "final") {
          setTranscript((current) =>
            current.map((entry) =>
              entry.id === assistantId && entry.role === "assistant"
                ? { ...entry, streaming: false, statusMessage: undefined, answer: event.data }
                : entry,
            ),
          );
          return;
        }
        if (event.type === "error") {
          const message =
            typeof event.data === "string"
              ? event.data
              : event.data.message ?? "Tutor chat streaming failed.";
          throw new Error(message);
        }
      });
      const finalEvent = [...events].reverse().find((event): event is Extract<TutorChatStreamEvent, { type: "final" }> => event.type === "final");
      if (!finalEvent) {
        throw new Error("Tutor chat stream finished without a final answer payload.");
      }
    } catch (submitError) {
      setTranscript((current) =>
        current.filter((entry) => !(entry.role === "assistant" && "streaming" in entry && entry.streaming)),
      );
      setError(getDisplayErrorMessage(submitError, "The tutor request failed."));
    } finally {
      setSubmitting(false);
    }
  }

  function handleQuestionKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) {
      return;
    }

    event.preventDefault();

    if (!selectedNotebookId || !question.trim() || submitting) {
      return;
    }

    event.currentTarget.form?.requestSubmit();
  }

  async function handleSaveAnswerToNotes(entry: Extract<TranscriptEntry, { role: "assistant" }>) {
    if (!selectedNotebookId) {
      setError("Choose an active notebook before saving a tutor answer.");
      return;
    }

    if (savedAnswerIds[entry.id] || savingAnswerIds[entry.id] || !entry.answer.content_markdown.trim()) {
      return;
    }

    setSavingAnswerIds((current) => ({ ...current, [entry.id]: true }));
    setError(null);

    try {
      const titleSeed = entry.prompt?.trim() || "Saved tutor answer";
      const safeTitle = titleSeed.length > 72 ? `${titleSeed.slice(0, 69)}...` : titleSeed;
      const content = [
        entry.prompt ? `## Question\n\n${entry.prompt}` : null,
        "## Answer",
        entry.answer.content_markdown,
      ]
        .filter(Boolean)
        .join("\n\n");

      await apiClient.createNote(selectedNotebookId, {
        title: safeTitle,
        content_markdown: content,
        note_type: "saved_answer",
        visibility: "private",
      });
      setSavedAnswerIds((current) => ({ ...current, [entry.id]: true }));
    } catch (saveError) {
      setError(getDisplayErrorMessage(saveError, "Unable to save tutor answer to notes."));
    } finally {
      setSavingAnswerIds((current) => {
        const next = { ...current };
        delete next[entry.id];
        return next;
      });
    }
  }

  async function handleAttachFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    event.target.value = "";

    if (!selectedNotebookId || !file) {
      return;
    }

    setUploadingSource(true);
    setSourceError(null);
    setUploadStatusMessage(`Uploading ${file.name}...`);

    try {
      const checksum = await hashFile(file);
      const mimeType = inferMimeType(file);
      const uploadUrl = await apiClient.uploadSourceObjectWithFallback(selectedNotebookId, {
        filename: file.name,
        mime_type: mimeType,
        byte_size: file.size,
        checksum_sha256: checksum,
      }, file);

      const source = await apiClient.createSource(selectedNotebookId, {
        upload_intent_id: uploadUrl.upload_intent_id,
        source_type: inferSourceType(file.name),
        title: file.name.replace(/\.[^.]+$/, "") || file.name,
        original_filename: file.name,
        storage_key: uploadUrl.storage_key,
        mime_type: mimeType,
        checksum_sha256: checksum,
        byte_size: file.size,
        language_code: "en",
      });

      setSources((current) => [source, ...current.filter((entry) => entry.id !== source.id)]);
      setUploadStatusMessage(`Queued ${file.name} for ingestion.`);
    } catch (uploadError) {
      setSourceError(getDisplayErrorMessage(uploadError, "Unable to attach file as a source."));
      setUploadStatusMessage(null);
    } finally {
      setUploadingSource(false);
    }
  }

  function toggleSourceSelection(sourceId: string) {
    setSelectedSourceIds((current) =>
      current.includes(sourceId) ? current.filter((id) => id !== sourceId) : [...current, sourceId],
    );
  }

  return (
    <section className={`tutor-shell ${isChatIdle ? "tutor-shell--idle" : ""}`}>
      {error ? (
        <div className="workspace-toast workspace-toast--error tutor-toast" role="alert" aria-live="assertive">
          <div className="workspace-toast__copy">
            <strong>Tutor request failed</strong>
            <p>{error}</p>
          </div>
          <button
            className="workspace-icon-button workspace-icon-button--toast"
            type="button"
            onClick={() => setError(null)}
            aria-label="Dismiss error"
          >
            ×
          </button>
        </div>
      ) : null}

      <div className="tutor-shell__utility">
        <div className="workspace-scope-wrap" ref={sourceScopeRef}>
          <button
            className={`workspace-scope-button tutor-scope-button ${sourceScopeOpen ? "is-open" : ""}`}
            type="button"
            onClick={() => setSourceScopeOpen((current) => !current)}
            disabled={!selectedNotebook || indexedSources.length === 0}
          >
            {sourceScopeLabel}
          </button>

          {sourceScopeOpen ? (
            <div className="workspace-scope-popover tutor-scope-popover">
              <div className="workspace-scope-popover__head">
                <strong>Source scope</strong>
                <button
                  className="workspace-action workspace-action--ghost"
                  type="button"
                  onClick={() => setSelectedSourceIds([])}
                >
                  Use all
                </button>
              </div>
              {indexedSources.length === 0 ? (
                <div className="workspace-empty">No indexed sources are ready yet.</div>
              ) : (
                <div className="workspace-scope-list">
                  {indexedSources.map((source) => (
                    <button
                      key={source.id}
                      className={`workspace-scope-option ${selectedSourceIds.includes(source.id) ? "is-active" : ""}`}
                      type="button"
                      onClick={() => toggleSourceSelection(source.id)}
                    >
                      <span>
                        <strong>{source.title}</strong>
                        <small>{source.source_type}</small>
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : null}
        </div>

        <Link className="workspace-action workspace-action--ghost tutor-shell__notes-link" to="/notes">
          Open notes
        </Link>
      </div>

      {chatSourcesOverlayOpen ? (
        <div className="tutor-sources-overlay">
          <div className="tutor-sources-overlay__backdrop" />
          <aside ref={sourcesOverlayRef} className="tutor-sources-desk" aria-label="Notebook sources">
            <div className="tutor-sources-desk__header">
              <div>
                <p className="workspace-kicker">Sources</p>
                <h3>Source desk</h3>
                <p className="workspace-muted">
                  Upload, retry, delete, and decide what the tutor should use without leaving the conversation.
                </p>
              </div>
              <div className="tutor-sources-desk__actions">
                <button
                  className="workspace-action workspace-action--ghost"
                  type="button"
                  onClick={() => setSelectedSourceIds([])}
                  disabled={indexedSources.length === 0}
                >
                  Use all
                </button>
                <button
                  className="workspace-icon-button"
                  type="button"
                  onClick={() => setChatSourcesOverlayOpen(false)}
                  aria-label="Close sources"
                >
                  ×
                </button>
              </div>
            </div>

            <div className="tutor-sources-desk__metrics">
              <span className="workspace-status workspace-status--indexed">{indexedSourceCount} indexed</span>
              <span className="workspace-status workspace-status--processing">{processingSources.length} processing</span>
              <span className="workspace-status workspace-status--failed">{failedSources.length} failed</span>
            </div>

            <div className="tutor-sources-desk__toolbar">
              <button
                className="workspace-action workspace-action--ghost"
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={!selectedNotebook || uploadingSource || submitting}
              >
                {uploadingSource ? "Adding source..." : "Add source"}
              </button>
              {uploadStatusMessage ? <span className="workspace-muted">{uploadStatusMessage}</span> : null}
            </div>

            {sourceError ? <p className="workspace-error">{sourceError}</p> : null}

            <div className="tutor-sources-desk__list">
              {activeSources.length === 0 ? (
                <div className="workspace-empty">No notebook sources are registered yet.</div>
              ) : (
                activeSources.map((source) => {
                  const isSelected = selectedSourceIds.includes(source.id);
                  const indexed = source.status === "indexed";

                  return (
                    <article key={source.id} className={`tutor-source-card tutor-source-card--${source.status}`}>
                      <div className="tutor-source-card__head">
                        <div className="tutor-source-card__copy">
                          <strong>{source.title}</strong>
                          <span>
                            {source.source_type.toUpperCase()} · {(source.byte_size / 1024).toFixed(1)} KB
                          </span>
                        </div>
                        <span className={`workspace-status workspace-status--${indexed ? "indexed" : source.status}`}>
                          {isRetryingSource(source) ? "retrying" : source.status}
                        </span>
                      </div>

                      <div className="tutor-source-card__actions">
                        {indexed ? (
                          <button
                            className={`workspace-action workspace-action--ghost ${isSelected ? "is-active" : ""}`}
                            type="button"
                            onClick={() => toggleSourceSelection(source.id)}
                          >
                            {isSelected ? "In scope" : "Use in tutor"}
                          </button>
                        ) : null}
                        <button
                          className="workspace-action workspace-action--ghost"
                          type="button"
                          onClick={() => void handleSourceAction(source)}
                          disabled={busySourceId === source.id || isProcessingSource(source)}
                        >
                          {busySourceId === source.id ? "Working..." : getSourceActionLabel(source)}
                        </button>
                        <button
                          className="workspace-action workspace-action--ghost workspace-action--danger"
                          type="button"
                          onClick={() => void handleDeleteSource(source.id)}
                          disabled={busySourceId === source.id}
                        >
                          Delete
                        </button>
                      </div>
                    </article>
                  );
                })
              )}
            </div>
          </aside>
        </div>
      ) : null}

      <div className="tutor-thread">
        <div className="tutor-thread__viewport">
          {sourceError ? <p className="workspace-error tutor-thread__notice">{sourceError}</p> : null}
          {transcript.length === 0 ? (
            <div className="tutor-thread__empty">
              <h2>Ready when you are.</h2>
              <p>
                {selectedNotebook
                  ? "Ask grounded questions from your indexed material and turn strong answers into notes."
                  : "Choose a notebook to start a grounded tutoring session."}
              </p>
            </div>
          ) : (
            transcript.map((entry) =>
              entry.role === "user" ? (
                <div key={entry.id} className="tutor-message tutor-message--user">
                  <div className="tutor-bubble tutor-bubble--user">{entry.content}</div>
                </div>
              ) : (
                <div key={entry.id} className="tutor-message tutor-message--assistant">
                  <div className="tutor-response">
                    {entry.answer.content_markdown.trim() ? (
                      <TutorMarkdown content={entry.answer.content_markdown} />
                    ) : entry.streaming ? (
                      <div className="workspace-message__status">
                        <span className="workspace-typing" aria-hidden="true">
                          <span />
                          <span />
                          <span />
                        </span>
                        <span>{entry.statusMessage ?? "Tutor is thinking..."}</span>
                      </div>
                    ) : null}
                    {!entry.streaming && entry.answer.content_markdown.trim() ? (
                      <div className="workspace-answer-actions tutor-response__actions">
                        <button
                          className="workspace-action workspace-action--ghost"
                          type="button"
                          onClick={() => void handleSaveAnswerToNotes(entry)}
                          disabled={Boolean(savedAnswerIds[entry.id]) || Boolean(savingAnswerIds[entry.id])}
                        >
                          {savedAnswerIds[entry.id]
                            ? "Saved to notes"
                            : savingAnswerIds[entry.id]
                              ? "Saving note..."
                              : "Save to notes"}
                        </button>
                        <button
                          className="workspace-action workspace-action--ghost"
                          type="button"
                          onClick={() =>
                            navigate("/notes", {
                              state: {
                                draft: createStudyDraftFromTutor(entry.prompt, entry.answer.content_markdown),
                              },
                            })
                          }
                        >
                          Turn into study note
                        </button>
                        <Link className="workspace-action workspace-action--ghost" to="/quizzes">
                          Generate quiz
                        </Link>
                      </div>
                    ) : null}
                    {entry.answer.refusal_reason ? (
                      <p className="workspace-error">{entry.answer.refusal_reason}</p>
                    ) : null}
                    <div className="workspace-citation-row">
                      {entry.answer.citations.length === 0 ? (
                        <span className="workspace-muted">No citations attached to this answer.</span>
                      ) : (
                        entry.answer.citations.map((citation) => (
                          <span key={citation.id} className="workspace-citation-chip">
                            {citation.display_label}
                          </span>
                        ))
                      )}
                    </div>
                    {(entry.sourcePreviews?.length ?? 0) > 0 ? (
                      <div className="evidence-list">
                        {entry.sourcePreviews?.map((preview) => (
                          <article key={`${preview.chunk_id}-${preview.source_id}`} className="evidence-card">
                            <strong>{preview.display_label}</strong>
                            <p>{preview.quote_text}</p>
                          </article>
                        ))}
                      </div>
                    ) : null}
                    {entry.answer.retrieval_trace ? (
                      <div className="workspace-trace">
                        <div className="workspace-trace__meta">
                          <span>Mode: {entry.answer.retrieval_trace.retrieval_mode}</span>
                          <span>Latency: {entry.answer.retrieval_trace.latency_ms} ms</span>
                        </div>
                        <div className="workspace-trace__items">
                          {entry.answer.retrieval_trace.items.map((item) => (
                            <article key={item.chunk_id} className="workspace-trace-card">
                              <strong>{item.chunk_id}</strong>
                              <span>Source {item.source_id}</span>
                              <span>
                                Rank {item.rank_before}
                                {item.rank_after ? ` -> ${item.rank_after}` : ""}
                              </span>
                              <span>Score {item.initial_score.toFixed(2)}</span>
                            </article>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </div>
                </div>
              ),
            )
          )}
        </div>

        <form className="tutor-composer" onSubmit={handleSubmit}>
          <input
            ref={fileInputRef}
            className="workspace-hidden-file-input"
            type="file"
            accept=".pdf,.docx,.pptx,.txt,.md"
            onChange={(event) => void handleAttachFile(event)}
            disabled={!selectedNotebook || uploadingSource || submitting}
          />

          <div className="tutor-composer__surface">
            <textarea
              className="workspace-composer__input workspace-composer__input--chat tutor-composer__input"
              rows={3}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={handleQuestionKeyDown}
              placeholder={
                selectedNotebook
                  ? "Ask anything about your material"
                  : "Select a notebook before sending a question."
              }
              disabled={!selectedNotebook || submitting}
            />

            <div className="tutor-composer__controls">
              <div className="tutor-composer__left">
                <button
                  className="workspace-attach tutor-composer__attach"
                  type="button"
                  aria-label="Attach source document"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={!selectedNotebook || uploadingSource || submitting}
                >
                  <PaperclipIcon />
                </button>
                <span className="workspace-muted tutor-composer__status">
                  {selectedNotebook
                    ? uploadStatusMessage ?? (sourcesLoading ? "Loading sources..." : `${indexedSourceCount} indexed sources ready`)
                    : "Notebook required"}
                </span>
              </div>

              <div className="tutor-composer__right">
                <span className="workspace-muted tutor-composer__status">
                  {uploadingSource ? "Adding source..." : submitting ? "Thinking..." : "Grounded answers only"}
                </span>
                <button
                  className="workspace-send workspace-send--icon tutor-composer__send"
                  type="submit"
                  disabled={!selectedNotebook || submitting}
                  aria-label={submitting ? "Sending question" : "Send question"}
                >
                  <SendIcon />
                </button>
              </div>
            </div>
          </div>
        </form>
      </div>
    </section>
  );
}
