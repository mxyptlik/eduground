import type { Role } from "@/lib/api/types";

export type RoleExperience = {
  label: string;
  focus: string;
  homeDescription: string;
  heroTitle: string;
  heroDescription: string;
  primaryAction: {
    label: string;
    to: string;
  };
  secondaryAction: {
    label: string;
    to: string;
  };
  analyticsDescription: string;
  notebookDescription: string;
};

const defaultExperience: RoleExperience = {
  label: "Member",
  focus: "Stay grounded in real material and keep the study loop moving.",
  homeDescription:
    "EduGround works best as a source-grounded study system: review material, ask the tutor, turn answers into notes, and convert those notes into practice.",
  heroTitle: "Keep the notebook moving from source to understanding.",
  heroDescription:
    "Use the active notebook as your study workspace, then move between chat, notes, and quizzes without losing context.",
  primaryAction: {
    label: "Open tutor",
    to: "/chat",
  },
  secondaryAction: {
    label: "Open notes",
    to: "/notes",
  },
  analyticsDescription:
    "Analytics should answer whether the notebook is ready, being used, and producing meaningful study output.",
  notebookDescription:
    "Each notebook is a bounded study environment with its own sources, tutoring mode, and artifact flow.",
};

export function getRoleExperience(role: Role | null | undefined): RoleExperience {
  switch (role) {
    case "student":
      return {
        label: "Student",
        focus: "Use the tutor to understand, then lock the learning into notes and quizzes.",
        homeDescription:
          "Your best flow is simple: study from sources, ask grounded questions, save the strongest answers, and come back to practice later.",
        heroTitle: "Use this notebook as your active study lane.",
        heroDescription:
          "Ask the tutor when you need clarity, save what matters into notes, and turn repeated weak spots into quizzes you can revisit.",
        primaryAction: {
          label: "Resume tutor",
          to: "/chat",
        },
        secondaryAction: {
          label: "Open notes",
          to: "/notes",
        },
        analyticsDescription:
          "Analytics should help you see whether the notebook is ready and whether your study artifacts are actually accumulating.",
        notebookDescription:
          "Each notebook should feel like a personal study space with a clear objective, reliable sources, and an easy path back into work.",
      };
    case "ta":
      return {
        label: "Teaching assistant",
        focus: "Support learners with reliable sources, strong notes, and repeatable practice.",
        homeDescription:
          "Use EduGround to answer questions from source material, refine explanations into notes, and keep support workflows easy to resume.",
        heroTitle: "Keep this notebook ready for learners who need fast, grounded support.",
        heroDescription:
          "Check source readiness, test the tutor against the material, and shape reusable notes or quizzes that reduce repeated questions.",
        primaryAction: {
          label: "Open tutor",
          to: "/chat",
        },
        secondaryAction: {
          label: "Review sources",
          to: "/sources",
        },
        analyticsDescription:
          "Analytics should show whether learners have enough grounded material and whether the notebook is producing useful support artifacts.",
        notebookDescription:
          "Each notebook should feel ready for tutoring support, with clear scope, healthy sources, and artifacts that scale beyond one conversation.",
      };
    case "instructor":
      return {
        label: "Instructor",
        focus: "Shape the learning experience and turn explanations into durable teaching assets.",
        homeDescription:
          "EduGround should help you move from course material to trusted tutoring, then from tutoring into notes and assessments worth reusing.",
        heroTitle: "Run this notebook like a teaching workspace, not just a content folder.",
        heroDescription:
          "Validate source quality, pressure-test the tutor, and turn the best explanations into notes and quizzes that reinforce your course design.",
        primaryAction: {
          label: "Review sources",
          to: "/sources",
        },
        secondaryAction: {
          label: "Open quiz studio",
          to: "/quizzes",
        },
        analyticsDescription:
          "Analytics should help you see whether the notebook is operationally healthy and whether the learning loop is producing durable outputs.",
        notebookDescription:
          "Each notebook should read like a teaching environment with a clear goal, reliable material, and obvious downstream study actions.",
      };
    case "admin":
      return {
        label: "Admin",
        focus: "Keep the workspace healthy, trustworthy, and ready for everyone else to use.",
        homeDescription:
          "EduGround should make it obvious which workspaces are healthy, which sources are ready, and where people are actually creating durable study value.",
        heroTitle: "Run the notebook as a healthy system, not just a page collection.",
        heroDescription:
          "Monitor source readiness, track adoption, and make sure chat, notes, and quizzes are producing enough value for the people relying on this workspace.",
        primaryAction: {
          label: "Open analytics",
          to: "/analytics",
        },
        secondaryAction: {
          label: "Review source desk",
          to: "/sources",
        },
        analyticsDescription:
          "Analytics should prioritize notebook health, adoption, and artifact creation instead of turning into decorative dashboards.",
        notebookDescription:
          "Each notebook should be easy to audit, easy to recover, and easy to hand back to the people doing the actual studying or teaching.",
      };
    default:
      return defaultExperience;
  }
}
