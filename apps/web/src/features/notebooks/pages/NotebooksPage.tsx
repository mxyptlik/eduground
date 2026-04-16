import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { apiClient } from "@/lib/api/client";
import { getDisplayErrorMessage } from "@/lib/api/errors";
import { getRoleExperience } from "@/lib/session/roleExperience";
import { useAppSession } from "@/lib/session/AppSessionContext";

export function NotebooksPage() {
  const { notebooks, selectedNotebookId, selectNotebook, refreshNotebooks, user } = useAppSession();
  const roleExperience = getRoleExperience(user?.role);
  const [filter, setFilter] = useState<"all" | "private" | "shared">("all");
  const [sortBy, setSortBy] = useState<"title" | "sources" | "learners">("sources");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [visibility, setVisibility] = useState<"private" | "course" | "shared" | "institution">(
    "private",
  );
  const [policyMode, setPolicyMode] = useState<"teaching" | "assignment" | "exam">("teaching");
  const [submitting, setSubmitting] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);

  const visibleNotebooks = useMemo(() => {
    const filtered = notebooks.filter((notebook) => {
      if (filter === "private") {
        return notebook.visibility === "private";
      }
      if (filter === "shared") {
        return notebook.visibility !== "private";
      }
      return true;
    });

    return [...filtered].sort((left, right) => {
      if (sortBy === "sources") {
        return right.source_count - left.source_count || left.title.localeCompare(right.title);
      }
      if (sortBy === "learners") {
        return right.learner_count - left.learner_count || left.title.localeCompare(right.title);
      }
      return left.title.localeCompare(right.title);
    });
  }, [filter, notebooks, sortBy]);

  useEffect(() => {
    if (!isCreateModalOpen) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setIsCreateModalOpen(false);
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isCreateModalOpen]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!title.trim()) {
      setError("A title is required to create a notebook.");
      return;
    }

    setSubmitting(true);
    setStatusMessage(null);
    setError(null);

    try {
      const notebook = await apiClient.createNotebook({
        title: title.trim(),
        description: description.trim() || null,
        visibility,
        policy_mode: policyMode,
      });
      await refreshNotebooks();
      selectNotebook(notebook.id);
      setTitle("");
      setDescription("");
      setVisibility("private");
      setPolicyMode("teaching");
      setStatusMessage(`Notebook created: ${notebook.title}`);
      setIsCreateModalOpen(false);
    } catch (submitError) {
      setError(getDisplayErrorMessage(submitError, "Unable to create notebook."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="page-stack">
      <div className="notebook-library-controls notebook-library-controls--standalone">
        <div className="segmented-control" role="tablist" aria-label="Notebook filter">
          <button
            type="button"
            className={filter === "all" ? "segmented-control__button active" : "segmented-control__button"}
            onClick={() => setFilter("all")}
          >
            All
          </button>
          <button
            type="button"
            className={filter === "private" ? "segmented-control__button active" : "segmented-control__button"}
            onClick={() => setFilter("private")}
          >
            Private
          </button>
          <button
            type="button"
            className={filter === "shared" ? "segmented-control__button active" : "segmented-control__button"}
            onClick={() => setFilter("shared")}
          >
            Shared
          </button>
        </div>
        <label className="select-label">
          Sort
          <select
            className="select-input"
            value={sortBy}
            onChange={(event) => setSortBy(event.target.value as "title" | "sources" | "learners")}
          >
            <option value="sources">Most sources</option>
            <option value="learners">Most learners</option>
            <option value="title">Title</option>
          </select>
        </label>
        <button
          className="notebook-create-trigger"
          type="button"
          aria-label="Create notebook"
          onClick={() => {
            setError(null);
            setIsCreateModalOpen(true);
          }}
        >
          +
        </button>
      </div>

      {statusMessage ? <p className="empty-state">{statusMessage}</p> : null}

      <section className="section-stack">
        <div className="section-heading">
          <div>
            <p className="card-eyebrow">Notebook list</p>
            <h3>Choose the workspace you want to reopen.</h3>
          </div>
        </div>
        {visibleNotebooks.length === 0 ? (
          <article className="portfolio-card portfolio-card--empty">
            <strong>No notebooks match this view</strong>
            <p>Create your first workspace above and it will appear here instantly.</p>
          </article>
        ) : (
          <div className="notebook-browser">
            {visibleNotebooks.map((notebook) => (
              <article
                key={notebook.id}
                className={`notebook-browser__item ${
                  selectedNotebookId === notebook.id ? "is-active" : ""
                }`}
              >
                <div className="notebook-browser__copy">
                  <div className="notebook-browser__head">
                    <span className="status-pill">
                      {selectedNotebookId === notebook.id ? "Current workspace" : "Notebook"}
                    </span>
                    <div className="notebook-browser__meta">
                      <span>{notebook.visibility}</span>
                      <span>{notebook.policy_mode}</span>
                    </div>
                  </div>
                  <div className="notebook-browser__title">
                    <strong>{notebook.title}</strong>
                    <p>{notebook.description ?? "Source-grounded study workspace"}</p>
                  </div>
                  <div className="notebook-browser__stats">
                    <span>{notebook.source_count} sources</span>
                    <span>{notebook.learner_count} learners</span>
                  </div>
                </div>
                <div className="notebook-browser__actions">
                  <button
                    className="cta-button secondary"
                    type="button"
                    onClick={() => selectNotebook(notebook.id)}
                    disabled={selectedNotebookId === notebook.id}
                  >
                    {selectedNotebookId === notebook.id ? "Open now" : "Open workspace"}
                  </button>
                  <Link className="cta-button secondary" to="/chat" onClick={() => selectNotebook(notebook.id)}>
                    Tutor
                  </Link>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      {isCreateModalOpen ? (
        <div
          className="notebook-modal-backdrop"
          role="presentation"
          onClick={() => setIsCreateModalOpen(false)}
        >
          <article
            className="notebook-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="create-notebook-title"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="notebook-modal__header">
              <div className="feature-panel__copy">
                <p className="card-eyebrow">Create notebook</p>
                <h3 id="create-notebook-title">Start a new workspace without leaving the library.</h3>
              </div>
              <button
                className="workspace-icon-button"
                type="button"
                aria-label="Close create notebook dialog"
                onClick={() => setIsCreateModalOpen(false)}
              >
                ×
              </button>
            </div>

            <form className="form-stack" onSubmit={handleSubmit}>
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Notebook title"
                disabled={submitting}
              />
              <textarea
                className="text-area"
                rows={4}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="What is this notebook for?"
                disabled={submitting}
              />
              <div className="inline-form">
                <label className="select-label">
                  Visibility
                  <select
                    className="select-input"
                    value={visibility}
                    onChange={(event) =>
                      setVisibility(
                        event.target.value as "private" | "course" | "shared" | "institution",
                      )
                    }
                    disabled={submitting}
                  >
                    <option value="private">Private</option>
                    <option value="course">Course</option>
                    <option value="shared">Shared</option>
                    <option value="institution">Institution</option>
                  </select>
                </label>
                <label className="select-label">
                  Policy mode
                  <select
                    className="select-input"
                    value={policyMode}
                    onChange={(event) =>
                      setPolicyMode(event.target.value as "teaching" | "assignment" | "exam")
                    }
                    disabled={submitting}
                  >
                    <option value="teaching">Teaching</option>
                    <option value="assignment">Assignment</option>
                    <option value="exam">Exam</option>
                  </select>
                </label>
              </div>
              {error ? <p className="error-banner">{error}</p> : null}
              <button className="cta-button" type="submit" disabled={submitting}>
                {submitting ? "Creating notebook..." : "Create notebook"}
              </button>
            </form>
          </article>
        </div>
      ) : null}
    </div>
  );
}
