import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, apiClient } from "@/lib/api/client";
import { getDisplayErrorMessage } from "@/lib/api/errors";
import type { Source } from "@/lib/api/types";
import { useAppSession } from "@/lib/session/AppSessionContext";

type FailedSourceConflictDetails = {
  existing_source_id?: string;
  latest_job?: Source["latest_job"];
};

const SOURCE_POLL_INTERVAL_MS = 5_000;

function isRetryJob(source: Source) {
  return source.latest_job?.job_type === "reindex";
}

function isRetryingSource(source: Source) {
  return (
    isRetryJob(source) &&
    (source.latest_job?.status === "queued" ||
      source.latest_job?.status === "running" ||
      source.latest_job?.status === "retrying")
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

function getSourceActionLabel(source: Source) {
  if (source.status === "failed") {
    return "Retry";
  }
  if (isRetryingSource(source)) {
    return "Retrying...";
  }
  if (
    source.status === "processing" ||
    source.latest_job?.status === "queued" ||
    source.latest_job?.status === "running" ||
    source.latest_job?.status === "retrying"
  ) {
    return "Processing...";
  }
  return "Reindex";
}

function getLatestJobSummary(source: Source) {
  const job = source.latest_job;
  if (!job) {
    return "No ingestion job yet.";
  }
  const jobLabel = job.job_type === "reindex" ? "Retry" : "Job";
  const status = `${jobLabel}: ${job.status}`;
  const stage = `Stage: ${job.stage ?? "n/a"}`;
  const attempts = `Attempts: ${job.attempt_count}`;
  return `${status} - ${stage} - ${attempts}`;
}

function getLatestJobError(source: Source) {
  const job = source.latest_job;
  if (!job || job.status !== "failed" || !job.error_message) {
    return null;
  }
  return job.error_message;
}

function getSourceStateTone(source: Source) {
  if (source.status === "failed") {
    return "failure";
  }
  if (isProcessingSource(source)) {
    return "active";
  }
  if (source.status === "indexed") {
    return "success";
  }
  return "neutral";
}

function isProcessingSource(source: Source) {
  return (
    source.status === "processing" ||
    source.latest_job?.status === "queued" ||
    source.latest_job?.status === "running" ||
    source.latest_job?.status === "retrying"
  );
}

function getDisplayedSourceStatus(source: Source) {
  if (isRetryingSource(source)) {
    return "retrying";
  }
  return source.status;
}

export function SourcesPage() {
  const { notebooks, selectedNotebookId, refreshNotebooks } = useAppSession();
  const selectedNotebook = notebooks.find((notebook) => notebook.id === selectedNotebookId) ?? null;
  const [sources, setSources] = useState<Source[]>([]);
  const [loading, setLoading] = useState(true);
  const [busySourceId, setBusySourceId] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [sourceTitle, setSourceTitle] = useState("");
  const [creating, setCreating] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refreshRequestIdRef = useRef(0);
  const pollingInFlightRef = useRef(false);
  const lastRevisionRef = useRef<string | null>(null);

  async function refreshSources(notebookId: string, options?: { silent?: boolean }) {
    const requestId = ++refreshRequestIdRef.current;
    const silent = Boolean(options?.silent);

    if (!silent) {
      setLoading(true);
      setError(null);
    }

    try {
      const sourceList = await apiClient.listSources(notebookId);
      if (requestId === refreshRequestIdRef.current) {
        setSources(sourceList);
      }
    } catch (loadError) {
      if (requestId === refreshRequestIdRef.current) {
        setError(getDisplayErrorMessage(loadError, "Unable to load sources."));
      }
      throw loadError;
    } finally {
      if (!silent && requestId === refreshRequestIdRef.current) {
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    lastRevisionRef.current = null;
    if (!selectedNotebookId) {
      setSources([]);
      setLoading(false);
      setError(null);
      return;
    }

    void refreshSources(selectedNotebookId).catch(() => {});
  }, [selectedNotebookId]);

  const hasProcessingSources = sources.some((source) => isProcessingSource(source));

  useEffect(() => {
    if (!selectedNotebookId || !hasProcessingSources) {
      return;
    }

    const notebookId = selectedNotebookId;
    let cancelled = false;
    let failures = 0;
    let timeoutId: number;
    async function poll() {
      if (cancelled) return;
      if (!document.hidden && !pollingInFlightRef.current) {
        pollingInFlightRef.current = true;
        try {
          const { revision } = await apiClient.getSourcesRevision(notebookId);
          if (cancelled) return;
          if (lastRevisionRef.current !== revision) {
            await refreshSources(notebookId, { silent: true });
            if (cancelled) return;
            lastRevisionRef.current = revision;
          }
          failures = 0;
        } catch {
          failures = Math.min(failures + 1, 4);
        } finally {
          pollingInFlightRef.current = false;
        }
      }
      if (!cancelled) timeoutId = window.setTimeout(poll,
        SOURCE_POLL_INTERVAL_MS * 2 ** failures + Math.random() * 500);
    }
    timeoutId = window.setTimeout(poll, SOURCE_POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
      refreshRequestIdRef.current += 1;
    };
  }, [selectedNotebookId, hasProcessingSources]);

  const readySources = useMemo(
    () => sources.filter((source) => source.status === "indexed"),
    [sources],
  );
  const processingSources = useMemo(
    () => sources.filter((source) => isProcessingSource(source)),
    [sources],
  );
  const attentionSources = useMemo(
    () => sources.filter((source) => source.status === "failed"),
    [sources],
  );
  const archivedSources = useMemo(
    () => sources.filter((source) => source.status === "archived" || source.status === "deleted"),
    [sources],
  );

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    setUploadFiles(files);
    const file = files[0] ?? null;
    setSelectedFile(file);
    if (file && !sourceTitle.trim()) {
      const nameWithoutExtension = file.name.replace(/\.[^.]+$/, "");
      setSourceTitle(nameWithoutExtension);
    }
  }

  async function registerSourceForFile(file: File): Promise<Source> {
    const checksum = await hashFile(file);
    const mimeType = inferMimeType(file);
    const uploadUrl = await apiClient.uploadSourceObjectWithFallback(selectedNotebookId!, {
      filename: file.name,
      mime_type: mimeType,
      byte_size: file.size,
      checksum_sha256: checksum,
    }, file);
    const title = uploadFiles.length > 1
      ? file.name.replace(/\.[^.]+$/, "")
      : sourceTitle.trim() || file.name;
    const source = await apiClient.createSource(selectedNotebookId!, {
      upload_intent_id: uploadUrl.upload_intent_id,
      source_type: inferSourceType(file.name),
      title,
      original_filename: file.name,
      storage_key: uploadUrl.storage_key,
      mime_type: mimeType,
      checksum_sha256: checksum,
      byte_size: file.size,
      language_code: "en",
    });
    setSources((current) => [source, ...current.filter((entry) => entry.id !== source.id)]);
    return source;
  }

  async function handleCreateSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const files = uploadFiles.length > 0 ? uploadFiles : selectedFile ? [selectedFile] : [];

    if (!selectedNotebookId || files.length === 0) {
      setError("Choose a file first.");
      return;
    }

    setCreating(true);
    setError(null);
    setStatusMessage(null);

    const registered: string[] = [];
    const failed: string[] = [];
    try {
      for (const file of files) {
        try {
          await registerSourceForFile(file);
          registered.push(file.name);
        } catch (actionError) {
          if (actionError instanceof ApiError && actionError.code === "failed_source_exists") {
            const details = actionError.details as FailedSourceConflictDetails | undefined;
            if (details?.existing_source_id) {
              void refreshSources(selectedNotebookId, { silent: true }).catch(() => {});
            }
            setError(actionError.message || "This file already exists as a failed source; retry that row instead.");
            setStatusMessage("The source list was refreshed so you can retry the failed row.");
            return;
          }
          failed.push(file.name);
          setError(getDisplayErrorMessage(actionError, `Unable to register ${file.name}.`));
        }
      }
      await refreshNotebooks();
      setSelectedFile(null);
      setUploadFiles([]);
      setSourceTitle("");
      if (registered.length > 0) {
        setStatusMessage(
          files.length === 1
            ? `Source queued for ingestion. Current status: ${registered[0]}.`
            : `${registered.length} of ${files.length} sources queued for ingestion${failed.length ? `; failed: ${failed.join(", ")}` : ""}.`,
        );
      } else if (failed.length > 0) {
        setStatusMessage(`No sources were queued. Failed: ${failed.join(", ")}.`);
      }
    } finally {
      setCreating(false);
    }
  }

  async function handleSourceAction(source: Source) {
    setBusySourceId(source.id);
    setError(null);
    setStatusMessage(null);
    try {
      const actionLabel = getSourceActionLabel(source);
      const job = await apiClient.reindexSource(source.id);
      setSources((current) =>
        current.map((entry) =>
          entry.id === source.id ? { ...entry, status: "processing", latest_job: job } : entry,
        ),
      );
      setStatusMessage(`${job.job_type === "reindex" ? "Retry" : actionLabel} queued: ${job.id}`);
    } catch (actionError) {
      setError(getDisplayErrorMessage(actionError, "Unable to queue reindex."));
    } finally {
      setBusySourceId(null);
    }
  }

  async function handleDelete(sourceId: string) {
    setBusySourceId(sourceId);
    setError(null);
    setStatusMessage(null);
    try {
      await apiClient.deleteSource(sourceId);
      await refreshNotebooks();
      setSources((current) => current.filter((source) => source.id !== sourceId));
      setStatusMessage("Source deleted.");
    } catch (actionError) {
      setError(getDisplayErrorMessage(actionError, "Unable to delete source."));
    } finally {
      setBusySourceId(null);
    }
  }

  return (
    <div className="page-stack">
      <section className="feature-split">
        <article className="feature-panel feature-panel--source-upload">
          <div className="feature-panel__copy">
            <p className="card-eyebrow">Add source</p>
            <h3>{selectedNotebook ? "Register material for grounded tutoring." : "Select a notebook"}</h3>
            <p>
              Upload once, then let the ingestion pipeline parse, chunk, embed, and index the
              material for grounded tutoring.
            </p>
          </div>
          <form className="form-stack form-stack--source-upload" onSubmit={handleCreateSource}>
            <input
              value={sourceTitle}
              onChange={(event) => setSourceTitle(event.target.value)}
              placeholder="Source title"
              disabled={!selectedNotebook || creating}
            />
            <input
              type="file"
              accept=".pdf,.docx,.pptx,.txt,.md"
              multiple
              onChange={handleFileChange}
              disabled={!selectedNotebook || creating}
            />
            {uploadFiles.length > 0 ? (
              <p className="muted">
                {uploadFiles.length === 1
                  ? `${uploadFiles[0].name} | ${(uploadFiles[0].size / 1024).toFixed(1)} KB`
                  : `${uploadFiles.length} files selected: ${uploadFiles.slice(0, 3).map((file) => file.name).join(", ")}${uploadFiles.length > 3 ? ", …" : ""}`}
              </p>
            ) : null}
            <button className="cta-button" type="submit" disabled={!selectedNotebook || creating}>
              {creating ? "Registering source..." : "Add source"}
            </button>
          </form>
        </article>
      </section>

      {statusMessage ? <p className="empty-state">{statusMessage}</p> : null}
      {error ? <p className="error-banner">{error}</p> : null}

      <section className="section-stack">
        <div className="section-heading">
          <div>
            <p className="card-eyebrow">Source desk</p>
            <h3>{selectedNotebook ? "Inspect what the tutor can trust." : "Select a notebook to inspect source material."}</h3>
          </div>
        </div>

        {loading ? <div className="empty-state">Loading notebook sources...</div> : null}
        {!loading && !error && sources.length === 0 ? (
          <div className="empty-state">
            No sources are registered for this notebook yet. Upload one above to start indexing real course material.
          </div>
        ) : null}

        {!loading && !error ? (
          <div className="source-groups">
            <SourceGroup
              title="Ready for tutoring"
              eyebrow="Indexed"
              sources={readySources}
              busySourceId={busySourceId}
              onSourceAction={handleSourceAction}
              onDelete={handleDelete}
            />
            <SourceGroup
              title="Still processing"
              eyebrow="Ingestion"
              sources={processingSources}
              busySourceId={busySourceId}
              onSourceAction={handleSourceAction}
              onDelete={handleDelete}
            />
            <SourceGroup
              title="Needs attention"
              eyebrow="Failures"
              sources={attentionSources}
              busySourceId={busySourceId}
              onSourceAction={handleSourceAction}
              onDelete={handleDelete}
            />
            {archivedSources.length > 0 ? (
              <SourceGroup
                title="Archived and removed"
                eyebrow="Inactive"
                sources={archivedSources}
                busySourceId={busySourceId}
                onSourceAction={handleSourceAction}
                onDelete={handleDelete}
              />
            ) : null}
          </div>
        ) : null}
      </section>
    </div>
  );
}

type SourceGroupProps = {
  title: string;
  eyebrow: string;
  sources: Source[];
  busySourceId: string | null;
  onSourceAction: (source: Source) => Promise<void>;
  onDelete: (sourceId: string) => Promise<void>;
};

function SourceGroup({ title, eyebrow, sources, busySourceId, onSourceAction, onDelete }: SourceGroupProps) {
  if (sources.length === 0) {
    return (
      <article className="source-group">
        <div className="section-heading section-heading--compact">
          <div>
            <p className="card-eyebrow">{eyebrow}</p>
            <h3>{title}</h3>
          </div>
        </div>
        <div className="empty-state">Nothing in this state right now.</div>
      </article>
    );
  }

  return (
    <article className="source-group">
      <div className="section-heading section-heading--compact">
        <div>
          <p className="card-eyebrow">{eyebrow}</p>
          <h3>{title}</h3>
        </div>
      </div>
      <div className="source-group__list">
        {sources.map((source) => {
          const latestJobError = getLatestJobError(source);
          const actionLabel = getSourceActionLabel(source);
          const stateTone = getSourceStateTone(source);
          const displayedStatus = getDisplayedSourceStatus(source);

          return (
            <article key={source.id} className={`source-card source-card--${stateTone}`}>
              <div className="source-card__header">
                <div>
                  <strong>{source.title}</strong>
                  <p>{source.source_type.toUpperCase()} | {(source.byte_size / 1024).toFixed(1)} KB</p>
                </div>
                <div className="source-card__chips">
                  <span className={`status-chip status-chip--${stateTone}`}>{displayedStatus}</span>
                  <span className="status-chip">{source.latest_job?.stage ?? "no stage"}</span>
                </div>
              </div>
              <p className="source-card__path">{source.storage_key}</p>
              <p className="source-card__summary">{getLatestJobSummary(source)}</p>
              {latestJobError ? <p className="error-banner source-card__error">{latestJobError}</p> : null}
              <div className="button-row">
                <button
                  className="cta-button secondary"
                  type="button"
                  onClick={() => void onSourceAction(source)}
                  disabled={
                    busySourceId === source.id ||
                    source.status === "deleted" ||
                    source.status === "processing" ||
                    source.latest_job?.status === "queued" ||
                    source.latest_job?.status === "running" ||
                    source.latest_job?.status === "retrying"
                  }
                >
                  {actionLabel}
                </button>
                <button
                  className="cta-button danger"
                  type="button"
                  onClick={() => void onDelete(source.id)}
                  disabled={busySourceId === source.id || source.status === "deleted"}
                >
                  Delete
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </article>
  );
}
