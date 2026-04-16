import { Link } from "react-router-dom";
import type { Notebook } from "@/lib/api/types";

type StudyPathRailProps = {
  activePathname: string;
  selectedNotebook: Notebook | null;
};

const pathItems = [
  {
    to: "/sources",
    label: "Sources",
    eyebrow: "01",
    description: "Bring in and validate the material the notebook can trust.",
  },
  {
    to: "/chat",
    label: "Chat",
    eyebrow: "02",
    description: "Ask grounded questions and move quickly from retrieval to understanding.",
  },
  {
    to: "/notes",
    label: "Notes",
    eyebrow: "03",
    description: "Capture the strongest answers as reusable study memory.",
  },
  {
    to: "/quizzes",
    label: "Quizzes",
    eyebrow: "04",
    description: "Turn notebook context into repeatable practice and revision.",
  },
];

function getItemMeta(label: string, selectedNotebook: Notebook | null) {
  if (label === "Sources") {
    return selectedNotebook ? `${selectedNotebook.source_count} in scope` : "Notebook required";
  }
  if (label === "Chat") {
    return selectedNotebook ? `${selectedNotebook.policy_mode} mode` : "Tutor idle";
  }
  if (label === "Notes") {
    return "Study memory";
  }
  return "Practice lane";
}

export function StudyPathRail({ activePathname, selectedNotebook }: StudyPathRailProps) {
  return (
    <section className="study-path-rail" aria-label="Notebook study path">
      <div className="study-path-rail__header">
        <div>
          <p className="card-eyebrow">Notebook path</p>
          <h3>Move through the notebook without losing the thread.</h3>
        </div>
        <span className="workspace-kicker">{selectedNotebook?.title ?? "No active notebook"}</span>
      </div>
      <div className="study-path-grid">
        {pathItems.map((item) => {
          const isActive = activePathname.startsWith(item.to);

          return (
            <Link
              key={item.to}
              className={`study-path-card ${isActive ? "is-active" : ""}`}
              to={item.to}
            >
              <div className="study-path-card__head">
                <span className="study-path-card__eyebrow">{item.eyebrow}</span>
                <span className="status-pill">{getItemMeta(item.label, selectedNotebook)}</span>
              </div>
              <strong>{item.label}</strong>
              <p>{item.description}</p>
            </Link>
          );
        })}
      </div>
    </section>
  );
}
