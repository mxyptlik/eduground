import type {
  ApiErrorShape,
  AuditLog,
  AuthUser,
  ChatSession,
  Evaluation,
  IngestionJob,
  Notebook,
  NotebookAnalytics,
  Note,
  QuizAttempt,
  Quiz,
  Source,
  TutorChatStreamEvent,
  TutorAnswer,
  UploadUrlResponse,
} from "@/lib/api/types";

type RequestOptions = RequestInit & {
  query?: Record<string, string | number | boolean | undefined>;
  timeoutMs?: number;
};

const DEFAULT_BASE_URL = "http://localhost:8000";

type AuthContextResolvers = {
  getToken?: () => Promise<string | null>;
  getActiveOrganizationId?: () => string | null;
};

type ErrorLikePayload = Partial<ApiErrorShape> & {
  detail?: string | { msg?: string } | Array<{ msg?: string }>;
};

export class ApiError extends Error {
  public readonly code?: string;
  public readonly requestId?: string | null;
  public readonly retryable: boolean;
  public readonly provider?: string | null;
  public readonly details?: unknown;

  constructor(
    message: string,
    public readonly status: number,
    options?: Partial<Pick<ApiErrorShape, "code" | "request_id" | "retryable" | "provider" | "details">>,
  ) {
    super(message);
    this.name = "ApiError";
    this.code = options?.code;
    this.requestId = options?.request_id;
    this.retryable = Boolean(options?.retryable);
    this.provider = options?.provider;
    this.details = options?.details;
  }
}

export class ApiClient {
  private authContext: AuthContextResolvers = {};

  constructor(private readonly baseUrl = import.meta.env.VITE_API_BASE_URL ?? DEFAULT_BASE_URL) {}

  getBaseUrl() {
    return this.baseUrl;
  }

  setAuthContext(authContext: AuthContextResolvers) {
    this.authContext = authContext;
  }

  clearAuthContext() {
    this.authContext = {};
  }

  private createUrl(path: string, query?: RequestOptions["query"]) {
    const url = new URL(path, this.baseUrl);

    if (query) {
      for (const [key, value] of Object.entries(query)) {
        if (value !== undefined) {
          url.searchParams.set(key, String(value));
        }
      }
    }

    return url;
  }

  private buildApiErrorFromPayload(status: number, payload: ErrorLikePayload | null, fallbackMessage: string) {
    if (payload && typeof payload.message === "string" && typeof payload.code === "string") {
        return new ApiError(payload.message, status, {
          code: payload.code,
          request_id: payload.request_id,
          retryable: payload.retryable,
          provider: payload.provider,
          details: payload.details,
        });
    }

    if (payload && typeof payload.detail === "string" && payload.detail.trim()) {
      return new ApiError(payload.detail, status);
    }

    if (payload && Array.isArray(payload.detail)) {
      const messages = payload.detail
        .map((entry) => (entry && typeof entry.msg === "string" ? entry.msg : null))
        .filter((value): value is string => Boolean(value));
      if (messages.length > 0) {
        return new ApiError(messages.join(" "), status, { details: payload.detail });
      }
    }

    if (
      payload &&
      payload.detail &&
      !Array.isArray(payload.detail) &&
      typeof payload.detail === "object" &&
      typeof payload.detail.msg === "string"
    ) {
      return new ApiError(payload.detail.msg, status, { details: payload.detail });
    }

    return new ApiError(fallbackMessage, status, { details: payload ?? undefined });
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const controller = new AbortController();
    const timeoutMs = options.timeoutMs ?? 12_000;
    const timeoutHandle = window.setTimeout(() => controller.abort(), timeoutMs);
    let response: Response;
    try {
      const token = await this.authContext.getToken?.();
      const activeOrganizationId = this.authContext.getActiveOrganizationId?.();
      response = await fetch(this.createUrl(path, options.query), {
        ...options,
        signal: controller.signal,
        credentials: "include",
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(activeOrganizationId ? { "X-Active-Organization-Id": activeOrganizationId } : {}),
          ...(options.body ? { "Content-Type": "application/json" } : {}),
          ...(options.headers ?? {}),
        },
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new ApiError(`Request to ${this.baseUrl} timed out. Check that the backend is running and finished starting up.`, 0, {
          code: "request_timeout",
          retryable: true,
        });
      }
      throw new ApiError(`Unable to reach API at ${this.baseUrl}. Check that the backend is running and CORS matches your web URL.`, 0, {
        code: "network_unreachable",
        retryable: true,
      });
    } finally {
      window.clearTimeout(timeoutHandle);
    }

    if (!response.ok) {
      const fallbackMessage = `Request failed with status ${response.status}`;
      try {
        const payload = (await response.json()) as ErrorLikePayload;
        throw this.buildApiErrorFromPayload(response.status, payload, fallbackMessage);
      } catch (error) {
        if (error instanceof ApiError) {
          throw error;
        }
        if (response.statusText.trim()) {
          throw new ApiError(`${fallbackMessage}: ${response.statusText}`, response.status);
        }
        throw new ApiError(fallbackMessage, response.status);
      }
    }

    if (response.status === 204) {
      return undefined as T;
    }

    return (await response.json()) as T;
  }

  async streamNdjson<T>(path: string, options: RequestOptions = {}, onEvent?: (event: T) => void): Promise<T[]> {
    const controller = new AbortController();
    const timeoutMs = options.timeoutMs ?? 60_000;
    const timeoutHandle = window.setTimeout(() => controller.abort(), timeoutMs);
    let response: Response;
    try {
      const token = await this.authContext.getToken?.();
      const activeOrganizationId = this.authContext.getActiveOrganizationId?.();
      response = await fetch(this.createUrl(path, options.query), {
        ...options,
        signal: controller.signal,
        credentials: "include",
        headers: {
          Accept: "application/x-ndjson",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(activeOrganizationId ? { "X-Active-Organization-Id": activeOrganizationId } : {}),
          ...(options.body ? { "Content-Type": "application/json" } : {}),
          ...(options.headers ?? {}),
        },
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new ApiError(`Request to ${this.baseUrl} timed out. Check that the backend is running and finished starting up.`, 0, {
          code: "request_timeout",
          retryable: true,
        });
      }
      throw new ApiError(`Unable to reach API at ${this.baseUrl}. Check that the backend is running and CORS matches your web URL.`, 0, {
        code: "network_unreachable",
        retryable: true,
      });
    } finally {
      window.clearTimeout(timeoutHandle);
    }

    if (!response.ok) {
      const fallbackMessage = `Request failed with status ${response.status}`;
      try {
        const payload = (await response.json()) as ErrorLikePayload;
        throw this.buildApiErrorFromPayload(response.status, payload, fallbackMessage);
      } catch (error) {
        if (error instanceof ApiError) {
          throw error;
        }
        throw new ApiError(fallbackMessage, response.status);
      }
    }

    if (!response.body) {
      return [];
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const events: T[] = [];

    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      let newlineIndex = buffer.indexOf("\n");
      while (newlineIndex >= 0) {
        const line = buffer.slice(0, newlineIndex).trim();
        buffer = buffer.slice(newlineIndex + 1);
        if (line) {
          const event = JSON.parse(line) as T;
          events.push(event);
          onEvent?.(event);
        }
        newlineIndex = buffer.indexOf("\n");
      }
    }

    const trailing = buffer.trim();
    if (trailing) {
      const event = JSON.parse(trailing) as T;
      events.push(event);
      onEvent?.(event);
    }

    return events;
  }
  getCurrentUser() {
    return this.request<AuthUser>("/api/auth/me", {
      timeoutMs: 60_000,
    });
  }

  listNotebooks() {
    return this.request<Notebook[]>("/api/notebooks", {
      timeoutMs: 60_000,
    });
  }

  createNotebook(payload: {
    title: string;
    description?: string | null;
    visibility?: "private" | "course" | "shared" | "institution";
    policy_mode?: "teaching" | "assignment" | "exam";
  }) {
    return this.request<Notebook>("/api/notebooks", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  listSources(notebookId: string) {
    return this.request<Source[]>(`/api/notebooks/${notebookId}/sources`, {
      timeoutMs: 45_000,
    });
  }

  createSourceUploadUrl(
    notebookId: string,
    payload: {
      filename: string;
      mime_type: string;
      byte_size: number;
      checksum_sha256: string;
      force_local_fallback?: boolean;
    },
  ) {
    return this.request<UploadUrlResponse>(`/api/notebooks/${notebookId}/sources/upload-url`, {
      method: "POST",
      body: JSON.stringify(payload),
      timeoutMs: 30_000,
    });
  }

  async uploadSourceObjectWithFallback(
    notebookId: string,
    payload: {
      filename: string;
      mime_type: string;
      byte_size: number;
      checksum_sha256: string;
    },
    file: File,
  ) {
    const uploadToSignedUrl = async (uploadUrl: string, provider: "r2" | "local_storage") => {
      let uploadResponse: Response;
      try {
        uploadResponse = await fetch(uploadUrl, {
          method: "PUT",
          headers: {
            "Content-Type": payload.mime_type,
          },
          body: file,
        });
      } catch {
        throw new ApiError("Upload target was unreachable.", 0, {
          code: "object_upload_failed",
          provider,
          retryable: true,
        });
      }

      if (!uploadResponse.ok) {
        throw new ApiError(`Upload failed with status ${uploadResponse.status}.`, uploadResponse.status, {
          code: "object_upload_failed",
          provider,
          retryable: uploadResponse.status >= 500 || uploadResponse.status === 429,
        });
      }
    };

    const primaryUploadUrl = await this.createSourceUploadUrl(notebookId, payload);
    try {
      await uploadToSignedUrl(primaryUploadUrl.upload_url, "r2");
      return primaryUploadUrl;
    } catch {
      const fallbackUploadUrl = await this.createSourceUploadUrl(notebookId, {
        ...payload,
        force_local_fallback: true,
      });
      await uploadToSignedUrl(fallbackUploadUrl.upload_url, "local_storage");
      return fallbackUploadUrl;
    }
  }

  createSource(
    notebookId: string,
    payload: {
      upload_intent_id: string;
      module_id?: string | null;
      source_type: "pdf" | "docx" | "pptx" | "txt" | "md" | "url" | "audio_transcript" | "derived_note";
      title: string;
      original_filename?: string | null;
      storage_key: string;
      mime_type: string;
      checksum_sha256: string;
      byte_size: number;
      language_code?: string | null;
    },
  ) {
    return this.request<Source>(`/api/notebooks/${notebookId}/sources`, {
      method: "POST",
      body: JSON.stringify(payload),
      timeoutMs: 30_000,
    });
  }

  deleteSource(sourceId: string) {
    return this.request<Source>(`/api/sources/${sourceId}`, {
      method: "DELETE",
    });
  }

  reindexSource(sourceId: string) {
    return this.request<IngestionJob>(`/api/sources/${sourceId}/reindex`, {
      method: "POST",
      body: JSON.stringify({ force: false }),
      timeoutMs: 30_000,
    });
  }

  listNotes(notebookId: string) {
    return this.request<Note[]>(`/api/notebooks/${notebookId}/notes`);
  }

  createNote(
    notebookId: string,
    payload: {
      title: string;
      content_markdown: string;
      note_type?: "manual" | "saved_answer" | "study_pack" | "faq" | "template";
      source_chat_message_id?: string | null;
      visibility?: "private" | "shared_notebook" | "instructor_only";
    },
  ) {
    return this.request<Note>(`/api/notebooks/${notebookId}/notes`, {
      method: "POST",
      body: JSON.stringify(payload),
      timeoutMs: 30_000,
    });
  }

  updateNote(noteId: string, payload: { title?: string; content_markdown?: string; visibility?: string }) {
    return this.request<Note>(`/api/notes/${noteId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
      timeoutMs: 30_000,
    });
  }

  deleteNote(noteId: string) {
    return this.request<void>(`/api/notes/${noteId}`, {
      method: "DELETE",
    });
  }

  convertNoteToSource(noteId: string) {
    return this.request<{ note_id: string; source_id: string; status: string }>(`/api/notes/${noteId}/convert-to-source`, {
      method: "POST",
    });
  }

  exportNote(noteId: string) {
    return this.request<{ note_id: string; export_format: string; content: string }>(`/api/notes/${noteId}/export`, {
      method: "POST",
    });
  }

  listQuizzes(notebookId: string) {
    return this.request<Quiz[]>(`/api/notebooks/${notebookId}/quizzes`);
  }

  generateQuiz(
    notebookId: string,
    payload: {
      title: string;
      difficulty: "easy" | "medium" | "hard" | "mixed";
      item_count: number;
      source_ids?: string[];
      note_ids?: string[];
      module_ids?: string[];
    },
  ) {
    return this.request<Quiz>(`/api/notebooks/${notebookId}/quizzes/generate`, {
      method: "POST",
      body: JSON.stringify(payload),
      timeoutMs: 45_000,
    });
  }

  createQuizAttempt(quizId: string) {
    return this.request<QuizAttempt>(`/api/quizzes/${quizId}/attempts`, {
      method: "POST",
    });
  }

  deleteQuiz(quizId: string) {
    return this.request<void>(`/api/quizzes/${quizId}`, {
      method: "DELETE",
    });
  }

  submitQuizAttempt(quizAttemptId: string, answers: Record<string, { choice: string }>) {
    return this.request<QuizAttempt>(`/api/attempts/${quizAttemptId}/submit`, {
      method: "POST",
      body: JSON.stringify({ answers }),
    });
  }

  getNotebookAnalytics(notebookId: string) {
    return this.request<NotebookAnalytics>(`/api/notebooks/${notebookId}/analytics`);
  }

  listAuditLogs() {
    return this.request<AuditLog[]>("/api/admin/audit-logs");
  }

  listEvaluations() {
    return this.request<Evaluation[]>("/api/admin/evaluations");
  }

  createChatSession(notebookId: string, title?: string) {
    return this.request<ChatSession>(`/api/notebooks/${notebookId}/chat/sessions`, {
      method: "POST",
      body: JSON.stringify({ title }),
    });
  }

  sendChatMessage(
    chatSessionId: string,
    payload: { content_markdown: string; selected_source_ids?: string[]; selected_module_ids?: string[] },
  ) {
    return this.request<TutorAnswer>(`/api/chat/sessions/${chatSessionId}/messages`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  streamChatMessage(
    chatSessionId: string,
    payload: { content_markdown: string; selected_source_ids?: string[]; selected_module_ids?: string[] },
    onEvent?: (event: TutorChatStreamEvent) => void,
  ) {
    return this.streamNdjson<TutorChatStreamEvent>(
      `/api/chat/sessions/${chatSessionId}/messages/stream`,
      {
        method: "POST",
        body: JSON.stringify(payload),
        timeoutMs: 120_000,
      },
      onEvent,
    );
  }
}

export const apiClient = new ApiClient();
