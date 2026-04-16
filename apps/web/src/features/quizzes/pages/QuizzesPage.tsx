import { Dispatch, FormEvent, SetStateAction, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { apiClient } from "@/lib/api/client";
import { getDisplayErrorMessage } from "@/lib/api/errors";
import type { Note, Quiz, QuizAttempt, QuizAttemptResultItem, Source } from "@/lib/api/types";
import { useAppSession } from "@/lib/session/AppSessionContext";

export function QuizzesPage() {
  const { notebooks, selectedNotebookId } = useAppSession();
  const selectedNotebook = notebooks.find((notebook) => notebook.id === selectedNotebookId) ?? null;
  const [quizzes, setQuizzes] = useState<Quiz[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [title, setTitle] = useState("Notebook review quiz");
  const [difficulty, setDifficulty] = useState<"easy" | "medium" | "hard" | "mixed">("mixed");
  const [itemCount, setItemCount] = useState(3);
  const [loading, setLoading] = useState(true);
  const [scopeLoading, setScopeLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [deletingQuizId, setDeletingQuizId] = useState<string | null>(null);
  const [activeQuizId, setActiveQuizId] = useState<string | null>(null);
  const [activeAttempt, setActiveAttempt] = useState<QuizAttempt | null>(null);
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<string, { choice: string }>>({});
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [selectedNoteIds, setSelectedNoteIds] = useState<string[]>([]);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const activeQuiz = useMemo(
    () => quizzes.find((quiz) => quiz.id === activeQuizId) ?? null,
    [activeQuizId, quizzes],
  );
  const activeResultsByItem = useMemo(
    () =>
      new Map(
        (activeAttempt?.results ?? []).map((result) => [result.quiz_item_id, result]),
      ),
    [activeAttempt],
  );
  const isSubmitted = activeAttempt?.status === "submitted";
  const activeQuestionCount = activeQuiz?.items.length ?? 0;
  const activeItem = activeQuiz?.items[currentQuestionIndex] ?? null;
  const activeItemResult = activeItem ? activeResultsByItem.get(activeItem.id) : undefined;

  useEffect(() => {
    const notebookId = selectedNotebookId;

    if (!notebookId) {
      setQuizzes([]);
      setLoading(false);
      return;
    }

    let ignore = false;
    const activeNotebookId: string = notebookId;

    async function loadQuizzes() {
      setLoading(true);
      setError(null);
      try {
        const quizList = await apiClient.listQuizzes(activeNotebookId);
        if (!ignore) {
          setQuizzes(quizList);
        }
      } catch (loadError) {
        if (!ignore) {
          setError(getDisplayErrorMessage(loadError, "Unable to load quizzes."));
        }
      } finally {
        if (!ignore) {
          setLoading(false);
        }
      }
    }

    void loadQuizzes();

    return () => {
      ignore = true;
    };
  }, [selectedNotebookId]);

  useEffect(() => {
    const notebookId = selectedNotebookId;

    if (!notebookId) {
      setSources([]);
      setNotes([]);
      setSelectedSourceIds([]);
      setSelectedNoteIds([]);
      setScopeLoading(false);
      return;
    }

    let ignore = false;
    const activeNotebookId: string = notebookId;

    async function loadScope() {
      setScopeLoading(true);
      try {
        const [sourceList, noteList] = await Promise.all([
          apiClient.listSources(activeNotebookId),
          apiClient.listNotes(activeNotebookId),
        ]);
        if (ignore) {
          return;
        }
        setSources(sourceList);
        setNotes(noteList);
        setSelectedSourceIds((current) => current.filter((id) => sourceList.some((source) => source.id === id)));
        setSelectedNoteIds((current) => current.filter((id) => noteList.some((note) => note.id === id)));
      } catch (loadError) {
        if (!ignore) {
          setError(getDisplayErrorMessage(loadError, "Unable to load notebook scope."));
        }
      } finally {
        if (!ignore) {
          setScopeLoading(false);
        }
      }
    }

    void loadScope();

    return () => {
      ignore = true;
    };
  }, [selectedNotebookId]);

  async function handleGenerate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!selectedNotebookId || !title.trim()) {
      return;
    }

    setSubmitting(true);
    setStatusMessage(buildGenerationStatus(selectedSourceIds.length, selectedNoteIds.length));
    setError(null);

    try {
      const quiz = await apiClient.generateQuiz(selectedNotebookId, {
        title: title.trim(),
        difficulty,
        item_count: itemCount,
        source_ids: selectedSourceIds,
        note_ids: selectedNoteIds,
      });
      setQuizzes((current) => [quiz, ...current]);
      setStatusMessage("Quiz generated.");
    } catch (submitError) {
      setError(getDisplayErrorMessage(submitError, "Unable to generate quiz."));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleStartAttempt(quiz: Quiz) {
    setSubmitting(true);
    setStatusMessage(`Opening ${quiz.title}...`);
    setError(null);
    try {
      const attempt = await apiClient.createQuizAttempt(quiz.id);
      setActiveQuizId(quiz.id);
      setActiveAttempt(attempt);
      setCurrentQuestionIndex(0);
      setAnswers({});
      setStatusMessage(`Attempt started for ${quiz.title}.`);
    } catch (attemptError) {
      setError(getDisplayErrorMessage(attemptError, "Unable to start quiz attempt."));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRetryAttempt() {
    if (!activeQuiz) {
      return;
    }
    await handleStartAttempt(activeQuiz);
  }

  async function handleDeleteQuiz(quiz: Quiz) {
    const confirmed = window.confirm(`Delete "${quiz.title}"? This will remove the quiz and its attempts.`);
    if (!confirmed) {
      return;
    }

    setDeletingQuizId(quiz.id);
    setError(null);
    setStatusMessage(`Deleting ${quiz.title}...`);

    try {
      await apiClient.deleteQuiz(quiz.id);
      setQuizzes((current) => current.filter((item) => item.id !== quiz.id));
      if (activeQuizId === quiz.id) {
        closeAttempt();
      }
      setStatusMessage(`Deleted ${quiz.title}.`);
    } catch (deleteError) {
      setError(getDisplayErrorMessage(deleteError, "Unable to delete quiz."));
    } finally {
      setDeletingQuizId(null);
    }
  }

  async function handleSubmitAttempt() {
    if (!activeAttempt || !activeQuizId) {
      return;
    }

    setSubmitting(true);
    setStatusMessage("Checking your answers against the notebook...");
    setError(null);

    try {
      const result = await apiClient.submitQuizAttempt(activeAttempt.id, answers);
      setActiveAttempt(result);
      setStatusMessage(`Quiz submitted. Score: ${result.score_percent ?? 0}%`);
    } catch (submitError) {
      setError(getDisplayErrorMessage(submitError, "Unable to submit quiz attempt."));
    } finally {
      setSubmitting(false);
    }
  }

  function closeAttempt() {
    setActiveQuizId(null);
    setActiveAttempt(null);
    setCurrentQuestionIndex(0);
    setAnswers({});
  }

  function setChoice(itemId: string, choice: string) {
    if (isSubmitted) {
      return;
    }
    setAnswers((current) => ({
      ...current,
      [itemId]: { choice },
    }));
  }

  function toggleSelection(id: string, setter: Dispatch<SetStateAction<string[]>>) {
    setter((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  }

  function goToPreviousQuestion() {
    setCurrentQuestionIndex((current) => Math.max(0, current - 1));
  }

  function goToNextQuestion() {
    setCurrentQuestionIndex((current) => {
      if (!activeQuiz) {
        return current;
      }
      return Math.min(activeQuiz.items.length - 1, current + 1);
    });
  }

  const indexedSources = sources.filter((source) => source.status === "indexed");
  const unconvertedNotes = notes.filter((note) => !note.converted_source_id);
  const scopeSummary =
    selectedSourceIds.length === 0 && selectedNoteIds.length === 0
      ? "Using the full notebook."
      : `${selectedSourceIds.length} source${selectedSourceIds.length === 1 ? "" : "s"} and ${selectedNoteIds.length} note${selectedNoteIds.length === 1 ? "" : "s"} selected.`;

  return (
    <div className="page-stack">
      <section className="feature-split">
        <article className="feature-panel">
          <div className="feature-panel__copy">
            <p className="card-eyebrow">Generate quiz</p>
            <h3>{selectedNotebook ? "Create a grounded quiz." : "Select a notebook"}</h3>
            <p>
              Generated quizzes become durable study artifacts. Use them after a tutoring session or note-writing pass to reinforce understanding.
            </p>
          </div>
          <form className="form-stack" onSubmit={handleGenerate}>
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Quiz title"
              disabled={!selectedNotebook || submitting}
            />
            <div className="inline-form">
              <label className="select-label">
                Difficulty
                <select
                  className="select-input"
                  value={difficulty}
                  onChange={(event) =>
                    setDifficulty(event.target.value as "easy" | "medium" | "hard" | "mixed")
                  }
                  disabled={!selectedNotebook || submitting}
                >
                  <option value="easy">Easy</option>
                  <option value="medium">Medium</option>
                  <option value="hard">Hard</option>
                  <option value="mixed">Mixed</option>
                </select>
              </label>
              <label className="select-label">
                Items
                <input
                  min={1}
                  max={10}
                  type="number"
                  value={itemCount}
                  onChange={(event) => setItemCount(Number(event.target.value))}
                  disabled={!selectedNotebook || submitting}
                />
              </label>
            </div>
            <div className="detail-list">
              <div>
                <strong>Quiz scope</strong>
                <p>{scopeSummary}</p>
              </div>
            </div>
            {statusMessage ? <p className="empty-state">{statusMessage}</p> : null}
            {error ? <p className="error-banner">{error}</p> : null}
            <button className="cta-button" type="submit" disabled={!selectedNotebook || submitting}>
              {submitting ? "Generating quiz..." : "Generate quiz"}
            </button>
          </form>
        </article>

        <article className="feature-panel feature-panel--soft">
          <p className="card-eyebrow">Quiz provenance</p>
          {scopeLoading ? <div className="empty-state">Loading notebook sources and notes...</div> : null}
          {!scopeLoading ? (
            <div className="section-stack section-stack--airy">
              <div className="selection-panel-group">
                <div className="section-heading">
                  <div>
                    <p className="card-eyebrow">Sources</p>
                    <h3>Choose indexed sources</h3>
                  </div>
                  {indexedSources.length > 0 ? (
                    <button className="cta-button secondary" type="button" onClick={() => setSelectedSourceIds([])}>
                      Use all
                    </button>
                  ) : null}
                </div>
                {indexedSources.length === 0 ? (
                  <div className="empty-state">No indexed sources are ready yet.</div>
                ) : (
                  <div className="selection-card-list selection-card-list--compact" role="list">
                    {indexedSources.map((source) => (
                      <button
                        key={source.id}
                        className={`selection-card ${selectedSourceIds.includes(source.id) ? "is-active" : ""}`}
                        type="button"
                        onClick={() => toggleSelection(source.id, setSelectedSourceIds)}
                      >
                        <span className="selection-card__content">
                          <strong>{source.title}</strong>
                          <small>{source.source_type}</small>
                        </span>
                        <span className="selection-card__meta">
                          <span className="selection-card__badge">
                            {selectedSourceIds.includes(source.id) ? "Scoped" : "Available"}
                          </span>
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div className="selection-panel-group">
                <div className="section-heading">
                  <div>
                    <p className="card-eyebrow">Notes</p>
                    <h3>Choose notebook notes</h3>
                  </div>
                  {unconvertedNotes.length > 0 ? (
                    <button className="cta-button secondary" type="button" onClick={() => setSelectedNoteIds([])}>
                      Use none
                    </button>
                  ) : null}
                </div>
                {unconvertedNotes.length === 0 ? (
                  <div className="empty-state">No notebook notes are available for quiz scope yet.</div>
                ) : (
                  <div className="selection-card-list selection-card-list--compact" role="list">
                    {unconvertedNotes.map((note) => (
                      <button
                        key={note.id}
                        className={`selection-card ${selectedNoteIds.includes(note.id) ? "is-active" : ""}`}
                        type="button"
                        onClick={() => toggleSelection(note.id, setSelectedNoteIds)}
                      >
                        <span className="selection-card__content">
                          <strong>{note.title}</strong>
                          <small>{note.visibility.replace("_", " ")}</small>
                        </span>
                        <span className="selection-card__meta">
                          <span className="selection-card__badge">
                            {selectedNoteIds.includes(note.id) ? "Scoped" : "Optional"}
                          </span>
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ) : null}
        </article>
      </section>

      <section className="section-stack">
        <div className="section-heading">
          <div>
            <p className="card-eyebrow">Quiz library</p>
            <h3>Resume or retry the practice that matters now.</h3>
          </div>
        </div>
        {loading ? <div className="empty-state">Loading quizzes...</div> : null}
        {!loading && quizzes.length === 0 ? (
          <div className="empty-state">No quizzes yet. Generate one to inspect grounded items.</div>
        ) : null}
        {!loading ? (
          <div className="portfolio-grid">
            {quizzes.map((quiz) => (
              <article key={quiz.id} className="portfolio-card quiz-card">
                <div className="portfolio-card__head">
                  <span className="status-pill">{quiz.status}</span>
                  <span className="workspace-kicker">{quiz.difficulty}</span>
                </div>
                <strong>{quiz.title}</strong>
                <p>
                  {quiz.items.length} items | Created {new Date(quiz.created_at).toLocaleString()}
                </p>
                <div className="portfolio-card__meta">
                  <span>Grounded quiz</span>
                  <span>Tutor generated</span>
                </div>
                <div className="button-row">
                  <button
                    className="cta-button secondary"
                    type="button"
                    onClick={() => void handleStartAttempt(quiz)}
                    disabled={submitting || deletingQuizId === quiz.id}
                  >
                    Open quiz runner
                  </button>
                  <button
                    className="cta-button danger"
                    type="button"
                    onClick={() => void handleDeleteQuiz(quiz)}
                    disabled={submitting || deletingQuizId === quiz.id}
                  >
                    {deletingQuizId === quiz.id ? "Deleting..." : "Delete quiz"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : null}
      </section>

      {activeQuiz && activeAttempt && activeItem ? (
        <section className="quiz-runner-shell" aria-label="Quiz runner">
          <div className="quiz-runner">
            <div className="quiz-runner__header">
              <span className="quiz-runner__counter">
                {currentQuestionIndex + 1} / {activeQuestionCount}
              </span>
              <button className="quiz-runner__close" type="button" onClick={closeAttempt} aria-label="Close quiz runner">
                ×
              </button>
            </div>

            {isSubmitted ? (
              <div className={`quiz-runner__summary ${activeAttempt.passed ? "is-pass" : "is-fail"}`}>
                <div>
                  <p className="card-eyebrow">{activeAttempt.passed ? "Pass" : "Needs another pass"}</p>
                  <h3>{activeAttempt.result_comment}</h3>
                </div>
                <div className="quiz-runner__score">
                  <strong>{Math.round(activeAttempt.score_percent ?? 0)}%</strong>
                  <span>
                    {activeAttempt.correct_count ?? 0}/{activeAttempt.total_items ?? activeQuiz.items.length} correct
                  </span>
                </div>
              </div>
            ) : null}

            <div className="quiz-runner__body">
              <article key={activeItem.id} className="quiz-question-card quiz-question-card--runner">
                <div className="quiz-question-card__head">
                  <span className="status-pill">Question {activeItem.position}</span>
                  {isSubmitted ? (
                    <span className={`status-pill ${activeItemResult?.is_correct ? "status-chip--success" : "status-chip--failure"}`}>
                      {activeItemResult?.is_correct ? "Correct" : "Incorrect"}
                    </span>
                  ) : null}
                </div>
                <h3>{activeItem.prompt_text}</h3>
                <div className="quiz-option-list">
                  {(activeItem.options_json ?? []).map((option, index) => {
                    const selected = answers[activeItem.id]?.choice === option;
                    return (
                      <button
                        key={option}
                        type="button"
                        className={getQuizOptionClassName(option, selected, activeItemResult)}
                        onClick={() => setChoice(activeItem.id, option)}
                        disabled={submitting || isSubmitted}
                      >
                        <span className="quiz-option__key">{String.fromCharCode(65 + index)}</span>
                        <span className="quiz-option__text">{option}</span>
                      </button>
                    );
                  })}
                </div>
                {isSubmitted ? (
                  <div className="quiz-feedback">
                    <p className={`quiz-feedback__headline ${activeItemResult?.is_correct ? "is-correct" : "is-incorrect"}`}>
                      {activeItemResult?.is_correct ? "You landed this one." : "This one needs another look."}
                    </p>
                    <p>{activeItemResult?.feedback_markdown ?? activeItem.rationale_markdown}</p>
                  </div>
                ) : null}
              </article>
            </div>

            <div className="quiz-runner__footer">
              <div className="button-row">
                <button
                  className="cta-button secondary"
                  type="button"
                  onClick={goToPreviousQuestion}
                  disabled={currentQuestionIndex === 0}
                >
                  Back
                </button>
                {currentQuestionIndex < activeQuestionCount - 1 ? (
                  <button className="cta-button secondary" type="button" onClick={goToNextQuestion}>
                    Next
                  </button>
                ) : null}
              </div>
              {!isSubmitted ? (
                currentQuestionIndex === activeQuestionCount - 1 ? (
                  <button
                    className="cta-button"
                    type="button"
                    onClick={handleSubmitAttempt}
                    disabled={submitting || !activeAttempt}
                  >
                    {submitting ? "Submitting attempt..." : "Submit attempt"}
                  </button>
                ) : (
                  <div className="quiz-runner__progress-copy">
                    {Object.keys(answers).length} of {activeQuestionCount} answered
                  </div>
                )
              ) : (
                <div className="button-row">
                  <button className="cta-button" type="button" onClick={() => void handleRetryAttempt()} disabled={submitting}>
                    Retry quiz
                  </button>
                  <button className="cta-button secondary" type="button" onClick={closeAttempt}>
                    Done
                  </button>
                </div>
              )}
            </div>
          </div>
        </section>
      ) : null}
    </div>
  );
}

function buildGenerationStatus(sourceCount: number, noteCount: number) {
  if (sourceCount === 0 && noteCount === 0) {
    return "Generating grounded quiz from the full notebook...";
  }
  if (sourceCount === 0) {
    return `Generating grounded quiz from ${noteCount} selected note${noteCount === 1 ? "" : "s"}...`;
  }
  if (noteCount === 0) {
    return `Generating grounded quiz from ${sourceCount} selected source${sourceCount === 1 ? "" : "s"}...`;
  }
  return `Generating grounded quiz from ${sourceCount} selected source${sourceCount === 1 ? "" : "s"} and ${noteCount} note${noteCount === 1 ? "" : "s"}...`;
}

function getQuizOptionClassName(option: string, selected: boolean, result?: QuizAttemptResultItem) {
  const classes = ["quiz-option"];
  const correctChoice = typeof result?.correct_answer_json?.choice === "string" ? result.correct_answer_json.choice : null;
  const submittedChoice = typeof result?.submitted_answer_json?.choice === "string" ? result.submitted_answer_json.choice : null;

  if (!result && selected) {
    classes.push("is-selected");
  }
  if (result && correctChoice === option) {
    classes.push("is-correct");
  }
  if (result && submittedChoice === option && correctChoice !== option) {
    classes.push("is-incorrect");
  }
  return classes.join(" ");
}
