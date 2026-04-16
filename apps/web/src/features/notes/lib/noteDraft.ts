export type NotesDraftPayload = {
  id: string;
  title: string;
  contentMarkdown: string;
  source: "tutor_chat";
};

export type NotesLocationState = {
  draft?: NotesDraftPayload;
} | null;

function stripMarkdown(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1")
    .replace(/[*_>#-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function buildDraftTitle(prompt: string | undefined): string {
  const plainPrompt = stripMarkdown(prompt ?? "");
  if (!plainPrompt) {
    return "Study note from tutor";
  }
  if (plainPrompt.length <= 72) {
    return plainPrompt;
  }
  return `${plainPrompt.slice(0, 69).trimEnd()}...`;
}

export function createStudyDraftFromTutor(prompt: string | undefined, answerMarkdown: string): NotesDraftPayload {
  const question = stripMarkdown(prompt ?? "");
  const trimmedAnswer = answerMarkdown.trim();
  const sections = [];

  if (question) {
    sections.push(`## Question\n\n${question}`);
  }

  if (trimmedAnswer) {
    sections.push(`## Study note\n\n${trimmedAnswer}`);
  }

  return {
    id: globalThis.crypto?.randomUUID?.() ?? `draft-${Date.now()}`,
    title: buildDraftTitle(prompt),
    contentMarkdown: sections.join("\n\n").trim(),
    source: "tutor_chat",
  };
}

export function getNotesDraftPayload(state: unknown): NotesDraftPayload | null {
  if (!state || typeof state !== "object") {
    return null;
  }

  const candidate = "draft" in state ? (state as { draft?: unknown }).draft : undefined;
  if (!candidate || typeof candidate !== "object") {
    return null;
  }

  const draft = candidate as Partial<NotesDraftPayload>;
  if (
    typeof draft.id !== "string" ||
    typeof draft.title !== "string" ||
    typeof draft.contentMarkdown !== "string" ||
    draft.source !== "tutor_chat"
  ) {
    return null;
  }

  return {
    id: draft.id,
    title: draft.title,
    contentMarkdown: draft.contentMarkdown,
    source: draft.source,
  };
}
