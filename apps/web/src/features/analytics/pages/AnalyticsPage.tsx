import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { apiClient } from "@/lib/api/client";
import { getDisplayErrorMessage } from "@/lib/api/errors";
import type { AuditLog, Evaluation, NotebookAnalytics } from "@/lib/api/types";
import { getRoleExperience } from "@/lib/session/roleExperience";
import { useAppSession } from "@/lib/session/AppSessionContext";

export function AnalyticsPage() {
  const { notebooks, selectedNotebookId, user } = useAppSession();
  const roleExperience = getRoleExperience(user?.role);
  const selectedNotebook = notebooks.find((notebook) => notebook.id === selectedNotebookId) ?? null;
  const [analytics, setAnalytics] = useState<NotebookAnalytics | null>(null);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [evaluations, setEvaluations] = useState<Evaluation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const notebookId = selectedNotebookId;
    const canViewAdmin = user?.role === "admin" || user?.role === "instructor";

    if (!notebookId) {
      setAnalytics(null);
      setLoading(false);
      return;
    }

    const activeNotebookId: string = notebookId;
    let ignore = false;

    async function loadAnalytics() {
      setLoading(true);
      setError(null);
      try {
        const [analyticsResponse, auditResponse, evaluationResponse] = await Promise.all([
          apiClient.getNotebookAnalytics(activeNotebookId),
          canViewAdmin ? apiClient.listAuditLogs() : Promise.resolve([]),
          canViewAdmin ? apiClient.listEvaluations() : Promise.resolve([]),
        ]);

        if (!ignore) {
          setAnalytics(analyticsResponse);
          setAuditLogs(auditResponse);
          setEvaluations(evaluationResponse);
        }
      } catch (loadError) {
        if (!ignore) {
          setError(getDisplayErrorMessage(loadError, "Unable to load analytics."));
        }
      } finally {
        if (!ignore) {
          setLoading(false);
        }
      }
    }

    void loadAnalytics();

    return () => {
      ignore = true;
    };
  }, [selectedNotebookId, user?.role]);

  const canViewAdmin = user?.role === "admin" || user?.role === "instructor";

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="Analytics"
        title="Keep notebook health visible without turning this into a dashboard circus."
        description={roleExperience.analyticsDescription}
        actions={
          <div className="button-row">
            <Link className="cta-button secondary" to="/sources">
              Review sources
            </Link>
            <Link className="cta-button secondary" to="/chat">
              Open tutor
            </Link>
          </div>
        }
      />

      {error ? <p className="error-banner">{error}</p> : null}
      {loading ? <div className="empty-state">Loading analytics...</div> : null}

      {!loading && analytics ? (
        <section className="hero-grid">
          <StatCard label="Sources" value={String(analytics.source_count)} detail="Registered sources" />
          <StatCard
            label="Indexed"
            value={String(analytics.indexed_source_count)}
            detail="Ready for retrieval"
          />
          <StatCard
            label="Chat sessions"
            value={String(analytics.chat_session_count)}
            detail={selectedNotebook ? `${selectedNotebook.title} usage` : "Notebook activity"}
          />
          <StatCard label="Notes" value={String(analytics.note_count)} detail="Learner memory captured" />
          <StatCard label="Quizzes" value={String(analytics.quiz_count)} detail="Grounded assessments" />
        </section>
      ) : null}

      {!loading && !analytics && !error ? (
        <div className="empty-state">Select a notebook to review health and activity metrics.</div>
      ) : null}

      <section className="section-stack">
        <div className="section-heading">
          <div>
            <p className="card-eyebrow">Operational read</p>
            <h3>Use analytics as a notebook health report.</h3>
          </div>
        </div>
        <div className="card-grid">
          <article className="info-card">
            <span className="card-eyebrow">{roleExperience.label} lens</span>
            <strong>Primary focus</strong>
            <p>{roleExperience.focus}</p>
          </article>
          <article className="info-card">
            <span className="card-eyebrow">Notebook health</span>
            <strong>Index coverage matters</strong>
            <p>Track how many sources are fully indexed before treating the notebook as reliable tutoring context.</p>
          </article>
          <article className="info-card">
            <span className="card-eyebrow">Tutor usage</span>
            <strong>Chat sessions are visible</strong>
            <p>Conversation counts give instructors and admins a quick read on adoption and support activity.</p>
          </article>
          <article className="info-card">
            <span className="card-eyebrow">Artifact flow</span>
            <strong>Notes and quizzes are the retention signal</strong>
            <p>If notes and quizzes are not growing, the tutor is not yet creating durable value.</p>
          </article>
        </div>
      </section>

      {canViewAdmin ? (
        <>
          <section className="section-stack">
            <div className="section-heading">
              <div>
                <p className="card-eyebrow">Admin trail</p>
                <h3>Recent audit logs</h3>
              </div>
            </div>
            {auditLogs.length === 0 ? (
              <div className="empty-state">No audit log entries available yet.</div>
            ) : (
              <div className="stack-list">
                {auditLogs.slice(0, 6).map((log) => (
                  <article key={log.id} className="row-card">
                    <div>
                      <strong>{log.action_type}</strong>
                      <p>
                        {log.resource_type} - {log.resource_id}
                      </p>
                    </div>
                    <div className="row-metrics">
                      <span>{log.notebook_id ?? "global"}</span>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>

          <section className="section-stack">
            <div className="section-heading">
              <div>
                <p className="card-eyebrow">Quality runs</p>
                <h3>Recent evaluations</h3>
              </div>
            </div>
            {evaluations.length === 0 ? (
              <div className="empty-state">No evaluation runs available yet.</div>
            ) : (
              <div className="stack-list">
                {evaluations.slice(0, 6).map((evaluation) => (
                  <article key={evaluation.id} className="row-card">
                    <div>
                      <strong>{evaluation.evaluation_type}</strong>
                      <p>{evaluation.model_profile_id}</p>
                    </div>
                    <div className="row-metrics">
                      <span>{evaluation.status}</span>
                      <span>{evaluation.notebook_id ?? "global"}</span>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}
