import { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { apiClient } from "@/lib/api/client";
import { getDisplayErrorMessage } from "@/lib/api/errors";
import { getNotesDraftPayload } from "@/features/notes/lib/noteDraft";
import type { Note } from "@/lib/api/types";
import { useAppSession } from "@/lib/session/AppSessionContext";

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function toInlineHtml(text: string): string {
  const escaped = escapeHtml(text);
  return escaped
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, "<code>$1</code>")
    .replace(/\[(.+?)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
}

function toEditableMarkup(content: string): string {
  const trimmed = content.trim();
  if (!trimmed) {
    return "";
  }

  const looksLikeHtml = /<\w+[^>]*>/.test(trimmed);
  if (looksLikeHtml) {
    return trimmed;
  }

  const lines = trimmed.split(/\r?\n/);
  const parts: string[] = [];
  let listBuffer: string[] = [];

  function flushList() {
    if (listBuffer.length > 0) {
      parts.push(`<ul>${listBuffer.join("")}</ul>`);
      listBuffer = [];
    }
  }

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flushList();
      continue;
    }
    if (line.startsWith("- ")) {
      listBuffer.push(`<li>${toInlineHtml(line.slice(2))}</li>`);
      continue;
    }
    flushList();
    if (line.startsWith("# ")) {
      parts.push(`<h1>${toInlineHtml(line.slice(2))}</h1>`);
      continue;
    }
    if (line.startsWith("> ")) {
      parts.push(`<blockquote>${toInlineHtml(line.slice(2))}</blockquote>`);
      continue;
    }
    parts.push(`<p>${toInlineHtml(line)}</p>`);
  }
  flushList();

  return parts.join("");
}

function extractPlainTextFromMarkup(markup: string): string {
  const container = document.createElement("div");
  container.innerHTML = markup;
  return (container.textContent ?? "").replace(/\s+/g, " ").trim();
}

function openPdfPrintPreview(title: string, html: string) {
  const previewWindow = window.open("", "_blank", "noopener,noreferrer,width=980,height=760");
  if (!previewWindow) {
    throw new Error("Allow pop-ups to export this note as PDF.");
  }

  const escapedTitle = escapeHtml(title);

  previewWindow.document.write(`<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>${escapedTitle}</title>
    <style>
      :root { color-scheme: light; }
      body {
        margin: 0;
        padding: 48px;
        font-family: "Segoe UI", Arial, sans-serif;
        color: #101215;
        background: #ffffff;
      }
      article {
        max-width: 820px;
        margin: 0 auto;
      }
      h1 {
        margin: 0 0 20px;
        font-size: 32px;
        line-height: 1.05;
      }
      .note-body {
        font-size: 15px;
        line-height: 1.7;
      }
      .note-body h1,
      .note-body h2,
      .note-body h3,
      .note-body h4 {
        margin-top: 28px;
        margin-bottom: 12px;
      }
      .note-body p,
      .note-body li {
        margin: 0 0 12px;
      }
      .note-body table {
        width: 100%;
        border-collapse: collapse;
      }
      .note-body th,
      .note-body td {
        padding: 10px 12px;
        border: 1px solid #d9dde3;
        text-align: left;
      }
      .note-body blockquote {
        margin: 0;
        padding-left: 16px;
        border-left: 3px solid #c8ced8;
        color: #3b4350;
      }
      .note-body code {
        padding: 2px 6px;
        border-radius: 6px;
        background: #f1f3f6;
      }
      .note-body pre {
        padding: 16px;
        overflow: auto;
        border-radius: 12px;
        background: #f1f3f6;
      }
      @media print {
        body { padding: 24px; }
      }
    </style>
  </head>
  <body>
    <article>
      <h1>${escapedTitle}</h1>
      <div class="note-body">${html}</div>
    </article>
  </body>
</html>`);
  previewWindow.document.close();
  previewWindow.focus();
  previewWindow.onload = () => {
    previewWindow.print();
  };
  previewWindow.onafterprint = () => {
    previewWindow.close();
  };
}

function ToolbarIcon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d={d} fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.9" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M4 7h16M9 4h6m-7 3 1 12h6l1-12M10 10v6M14 10v6"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.9"
      />
    </svg>
  );
}

const HIGHLIGHT_COLORS = ["#fff59d", "#b9f6ca", "#ffd1dc"] as const;
const DEFAULT_FONT_SIZE = 16;

type ToolbarState = {
  isBold: boolean;
  isItalic: boolean;
  isUnderline: boolean;
  isList: boolean;
  isQuote: boolean;
  isHeading: boolean;
  activeHighlight: string | null;
  activeFontSize: number;
};

const DEFAULT_TOOLBAR_STATE: ToolbarState = {
  isBold: false,
  isItalic: false,
  isUnderline: false,
  isList: false,
  isQuote: false,
  isHeading: false,
  activeHighlight: null,
  activeFontSize: DEFAULT_FONT_SIZE,
};

function clampFontSize(value: number): number {
  return Math.min(48, Math.max(10, value));
}

function toHexColor(colorValue: string | null): string | null {
  if (!colorValue) {
    return null;
  }

  const normalized = colorValue.trim().toLowerCase();
  if (!normalized || normalized === "transparent" || normalized === "none" || normalized === "initial") {
    return null;
  }

  if (normalized.startsWith("#")) {
    if (normalized.length === 4) {
      const r = normalized[1];
      const g = normalized[2];
      const b = normalized[3];
      return `#${r}${r}${g}${g}${b}${b}`;
    }
    return normalized.slice(0, 7);
  }

  const rgbMatch = normalized.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (!rgbMatch) {
    return null;
  }

  const [r, g, b] = rgbMatch.slice(1).map((channel) => Number.parseInt(channel, 10));
  if ([r, g, b].some((channel) => Number.isNaN(channel))) {
    return null;
  }

  return `#${r.toString(16).padStart(2, "0")}${g.toString(16).padStart(2, "0")}${b
    .toString(16)
    .padStart(2, "0")}`;
}

function getSelectionHostElement(selection: Selection | null): HTMLElement | null {
  if (!selection || selection.rangeCount === 0) {
    return null;
  }
  const node = selection.anchorNode;
  if (!node) {
    return null;
  }
  if (node instanceof HTMLElement) {
    return node;
  }
  return node.parentElement;
}

function cloneActiveRange(): Range | null {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) {
    return null;
  }
  return selection.getRangeAt(0).cloneRange();
}

function getSelectionFontSize(editorRoot: HTMLDivElement): number {
  const selection = window.getSelection();
  const host = getSelectionHostElement(selection);
  if (!host || !editorRoot.contains(host)) {
    return DEFAULT_FONT_SIZE;
  }
  const parsed = Number.parseFloat(window.getComputedStyle(host).fontSize);
  if (Number.isNaN(parsed)) {
    return DEFAULT_FONT_SIZE;
  }
  return clampFontSize(Math.round(parsed));
}

export function NotesPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { selectedNotebookId } = useAppSession();
  const [notes, setNotes] = useState<Note[]>([]);
  const [selectedNoteId, setSelectedNoteId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [contentMarkup, setContentMarkup] = useState("");
  const [editorMode, setEditorMode] = useState<"idle" | "draft" | "selected">("idle");
  const [isMemoryCollapsed, setIsMemoryCollapsed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [busyNoteId, setBusyNoteId] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toolbarState, setToolbarState] = useState<ToolbarState>(DEFAULT_TOOLBAR_STATE);
  const [fontSizeInput, setFontSizeInput] = useState(String(DEFAULT_FONT_SIZE));
  const editorRef = useRef<HTMLDivElement | null>(null);
  const toolbarStateReaderRef = useRef(DEFAULT_TOOLBAR_STATE);
  const consumedDraftIdRef = useRef<string | null>(null);
  const savedSelectionRef = useRef<Range | null>(null);
  const pendingFontSizeCommitRef = useRef<number | null>(null);

  const selectedNote = useMemo(
    () => notes.find((note) => note.id === selectedNoteId) ?? null,
    [notes, selectedNoteId],
  );
  const isIdle = editorMode === "idle";

  useEffect(() => {
    const notebookId = selectedNotebookId;

    if (!notebookId) {
      setNotes([]);
      setSelectedNoteId(null);
      setTitle("");
      setContentMarkup("");
      setEditorMode("idle");
      setLoading(false);
      return;
    }

    const activeNotebookId: string = notebookId;
    let ignore = false;

    async function loadNotes() {
      setLoading(true);
      setError(null);
      try {
        const noteList = await apiClient.listNotes(activeNotebookId);
        if (!ignore) {
          setNotes(noteList);
          setSelectedNoteId(null);
          setTitle("");
          setContentMarkup("");
          setEditorMode("idle");
        }
      } catch (loadError) {
        if (!ignore) {
          setError(getDisplayErrorMessage(loadError, "Unable to load notes."));
        }
      } finally {
        if (!ignore) {
          setLoading(false);
        }
      }
    }

    void loadNotes();

    return () => {
      ignore = true;
    };
  }, [selectedNotebookId]);

  useEffect(() => {
    if (!editorRef.current) {
      return;
    }
    if (editorRef.current.innerHTML !== contentMarkup) {
      editorRef.current.innerHTML = contentMarkup;
    }
  }, [contentMarkup, selectedNoteId]);

  useEffect(() => {
    const draft = getNotesDraftPayload(location.state);
    if (!selectedNotebookId || loading || !draft || consumedDraftIdRef.current === draft.id) {
      return;
    }

    const markup = toEditableMarkup(draft.contentMarkdown);
    consumedDraftIdRef.current = draft.id;
    setSelectedNoteId(null);
    setTitle(draft.title);
    setContentMarkup(markup);
    setEditorMode("draft");
    setStatusMessage("Tutor answer loaded into a study note draft.");
    setError(null);

    window.requestAnimationFrame(() => {
      if (!editorRef.current) {
        return;
      }
      editorRef.current.innerHTML = markup;
      editorRef.current.focus();
    });

    navigate(location.pathname, { replace: true, state: null });
  }, [loading, location.pathname, location.state, navigate, selectedNotebookId]);

  useEffect(() => {
    toolbarStateReaderRef.current = toolbarState;
  }, [toolbarState]);

  useEffect(() => {
    setToolbarState(DEFAULT_TOOLBAR_STATE);
    setFontSizeInput(String(DEFAULT_FONT_SIZE));
  }, [selectedNoteId, editorMode, selectedNotebookId]);

  function handleNewNote() {
    setSelectedNoteId(null);
    setTitle("");
    setContentMarkup("");
    setEditorMode("draft");
    setStatusMessage("Fresh draft ready.");
    setError(null);
    if (editorRef.current) {
      editorRef.current.innerHTML = "";
      editorRef.current.focus();
    }
  }

  function activateDraftFromEditor() {
    if (!selectedNotebookId || submitting || editorMode !== "idle") {
      return;
    }
    handleNewNote();
  }

  function handleSelectNote(note: Note) {
    setSelectedNoteId(note.id);
    setTitle(note.title);
    setContentMarkup(toEditableMarkup(note.content_markdown));
    setEditorMode("selected");
    setStatusMessage(null);
    setError(null);
  }

  function syncMarkupFromEditor() {
    const html = editorRef.current?.innerHTML ?? "";
    setContentMarkup(html);
  }

  function saveEditorSelection() {
    const editor = editorRef.current;
    const selection = window.getSelection();
    const host = getSelectionHostElement(selection);
    if (!editor || !host || !editor.contains(host)) {
      return;
    }
    savedSelectionRef.current = cloneActiveRange();
  }

  function restoreEditorSelection() {
    const editor = editorRef.current;
    const savedSelection = savedSelectionRef.current;
    if (!editor || !savedSelection) {
      return false;
    }

    const selection = window.getSelection();
    if (!selection) {
      return false;
    }

    editor.focus();
    selection.removeAllRanges();
    selection.addRange(savedSelection.cloneRange());
    return true;
  }

  function refreshToolbarState() {
    const editor = editorRef.current;
    const selection = window.getSelection();
    const host = getSelectionHostElement(selection);
    if (!editor || !host || !editor.contains(host)) {
      setToolbarState((current) =>
        current.isBold ||
        current.isItalic ||
        current.isUnderline ||
        current.isList ||
        current.isQuote ||
        current.isHeading ||
        current.activeHighlight
          ? { ...DEFAULT_TOOLBAR_STATE, activeFontSize: current.activeFontSize }
          : current,
      );
      return;
    }

    const formatValue = String(document.queryCommandValue("formatBlock") ?? "").toLowerCase();
    const highlightValue = toHexColor(String(document.queryCommandValue("hiliteColor") ?? ""));
    const nextState: ToolbarState = {
      isBold: document.queryCommandState("bold"),
      isItalic: document.queryCommandState("italic"),
      isUnderline: document.queryCommandState("underline"),
      isList: document.queryCommandState("insertUnorderedList"),
      isQuote: formatValue.includes("blockquote"),
      isHeading: formatValue.includes("h1"),
      activeHighlight: highlightValue && HIGHLIGHT_COLORS.includes(highlightValue as (typeof HIGHLIGHT_COLORS)[number]) ? highlightValue : null,
      activeFontSize: pendingFontSizeCommitRef.current ?? getSelectionFontSize(editor),
    };
    setToolbarState(nextState);
    setFontSizeInput(String(nextState.activeFontSize));
    saveEditorSelection();
    if (pendingFontSizeCommitRef.current !== null) {
      window.requestAnimationFrame(() => {
        pendingFontSizeCommitRef.current = null;
      });
    }
  }

  useEffect(() => {
    function handleSelectionChange() {
      refreshToolbarState();
    }
    document.addEventListener("selectionchange", handleSelectionChange);
    return () => {
      document.removeEventListener("selectionchange", handleSelectionChange);
    };
  }, []);

  function applyCommand(command: string, value?: string) {
    if (!editorRef.current || !selectedNotebookId || submitting) {
      return;
    }
    if (editorMode === "idle") {
      activateDraftFromEditor();
    }
    restoreEditorSelection() || editorRef.current.focus();
    document.execCommand("styleWithCSS", false, "true");
    document.execCommand(command, false, value);
    syncMarkupFromEditor();
    saveEditorSelection();
    refreshToolbarState();
  }

  function applyHighlight(color: string) {
    const normalizedColor = toHexColor(color);
    const activeColor = toolbarStateReaderRef.current.activeHighlight;
    const shouldDisableHighlight = normalizedColor !== null && activeColor === normalizedColor;
    applyCommand("hiliteColor", shouldDisableHighlight ? "transparent" : color);
  }

  function applyFontSize(sizePx: number) {
    if (!editorRef.current || !selectedNotebookId || submitting) {
      return;
    }
    if (editorMode === "idle") {
      activateDraftFromEditor();
    }
    pendingFontSizeCommitRef.current = sizePx;
    restoreEditorSelection() || editorRef.current.focus();
    document.execCommand("styleWithCSS", false, "true");
    document.execCommand("fontSize", false, "7");
    const fonts = editorRef.current.querySelectorAll('font[size="7"]');
    const replacementSpans: HTMLSpanElement[] = [];
    fonts.forEach((fontNode) => {
      const span = document.createElement("span");
      span.style.fontSize = `${sizePx}px`;
      span.innerHTML = fontNode.innerHTML;
      fontNode.replaceWith(span);
      replacementSpans.push(span);
    });
    if (replacementSpans.length > 0) {
      const selection = window.getSelection();
      if (selection) {
        const range = document.createRange();
        range.selectNodeContents(replacementSpans[replacementSpans.length - 1]);
        range.collapse(false);
        selection.removeAllRanges();
        selection.addRange(range);
      }
    }
    syncMarkupFromEditor();
    setToolbarState((current) => ({ ...current, activeFontSize: sizePx }));
    setFontSizeInput(String(sizePx));
    saveEditorSelection();
    refreshToolbarState();
  }

  function handleFontSizeInputChange(rawValue: string) {
    setFontSizeInput(rawValue);
  }

  function commitFontSizeInput(rawValue: string) {
    const parsed = Number.parseInt(rawValue, 10);
    if (Number.isNaN(parsed)) {
      setFontSizeInput(String(toolbarStateReaderRef.current.activeFontSize));
      return;
    }
    applyFontSize(clampFontSize(parsed));
  }

  function handleToolbarMouseDown(event: ReactMouseEvent<HTMLDivElement>) {
    const target = event.target as HTMLElement;
    if (target.closest(".workspace-toolbar-button")) {
      saveEditorSelection();
      event.preventDefault();
    }
  }

  function handleFontSizeMouseDown() {
    saveEditorSelection();
  }

  function handleFontSizeKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter") {
      return;
    }
    event.preventDefault();
    commitFontSizeInput(fontSizeInput);
  }

  async function handleSave() {
    if (!selectedNotebookId) {
      setError("Choose an active notebook before saving a note.");
      return;
    }

    if (!title.trim()) {
      setError("Add a note title before saving.");
      return;
    }

    const markup = editorRef.current?.innerHTML ?? contentMarkup;
    const plainText = extractPlainTextFromMarkup(markup);
    if (!plainText) {
      setError("Write something in the note before saving.");
      return;
    }

    setSubmitting(true);
    setStatusMessage("Saving note...");
    setError(null);

    try {
      if (selectedNoteId) {
        const updated = await apiClient.updateNote(selectedNoteId, {
          title: title.trim(),
          content_markdown: markup.trim(),
        });
        setNotes((current) => current.map((note) => (note.id === updated.id ? updated : note)));
        setSelectedNoteId(null);
        setTitle("");
        setContentMarkup("");
        if (editorRef.current) {
          editorRef.current.innerHTML = "";
        }
        setEditorMode("idle");
        setStatusMessage("Note updated. Select it from Study memory to edit again.");
      } else {
        const created = await apiClient.createNote(selectedNotebookId, {
          title: title.trim(),
          content_markdown: markup.trim(),
        });
        setNotes((current) => [created, ...current]);
        setSelectedNoteId(null);
        setTitle("");
        setContentMarkup("");
        if (editorRef.current) {
          editorRef.current.innerHTML = "";
        }
        setEditorMode("idle");
        setStatusMessage("Note saved. Select it from Study memory to edit again.");
      }
    } catch (saveError) {
      setError(getDisplayErrorMessage(saveError, "Unable to save the note."));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleConvert(noteId: string) {
    setBusyNoteId(noteId);
    setStatusMessage(null);
    setError(null);
    try {
      const result = await apiClient.convertNoteToSource(noteId);
      setNotes((current) =>
        current.map((note) =>
          note.id === noteId ? { ...note, converted_source_id: result.source_id } : note,
        ),
      );
      setStatusMessage("Derived source created from note.");
    } catch (convertError) {
      setError(getDisplayErrorMessage(convertError, "Unable to convert note."));
    } finally {
      setBusyNoteId(null);
    }
  }

  async function handleExport(note: Note) {
    setBusyNoteId(note.id);
    setStatusMessage(null);
    setError(null);
    try {
      const markup = editorRef.current?.innerHTML?.trim() || toEditableMarkup(note.content_markdown);
      const plainText = extractPlainTextFromMarkup(markup);
      if (!plainText) {
        throw new Error("Add content before exporting this note.");
      }
      openPdfPrintPreview(note.title, markup);
      setStatusMessage("PDF export opened. Choose Save as PDF in the print dialog.");
    } catch (exportError) {
      const message =
        exportError instanceof Error ? exportError.message : "Unable to export note as PDF.";
      setError(getDisplayErrorMessage(exportError, message));
    } finally {
      setBusyNoteId(null);
    }
  }

  async function handleDeleteNote(note: Note) {
    setBusyNoteId(note.id);
    setStatusMessage(null);
    setError(null);
    try {
      await apiClient.deleteNote(note.id);
      setNotes((current) => current.filter((entry) => entry.id !== note.id));
      if (selectedNoteId === note.id) {
        setSelectedNoteId(null);
        setTitle("");
        setContentMarkup("");
        setEditorMode("idle");
        if (editorRef.current) {
          editorRef.current.innerHTML = "";
        }
      }
      setStatusMessage("Note deleted.");
    } catch (deleteError) {
      setError(getDisplayErrorMessage(deleteError, "Unable to delete note."));
    } finally {
      setBusyNoteId(null);
    }
  }

  return (
    <section className="workspace-shell notebook-workspace">
      <div className="workspace-topbar">
        <div className="workspace-topbar__title">
          <span className="workspace-breadcrumb">Studio &gt; Note</span>
          <h2>Notes</h2>
        </div>
        <div className="workspace-topbar__actions">
          <button className="workspace-action" type="button" onClick={handleNewNote}>
            New note
          </button>
          <Link className="workspace-action workspace-action--ghost" to="/chat">
            Back to chat
          </Link>
        </div>
      </div>

      <div className={`workspace-grid workspace-grid--notes ${isMemoryCollapsed ? "workspace-grid--notes-collapsed" : ""}`}>
        {isMemoryCollapsed ? (
          <aside className="workspace-notes-collapsed-slot" aria-label="Study memory collapsed">
            <button
              className="workspace-icon-button workspace-icon-button--notes-rail"
              type="button"
              onClick={() => setIsMemoryCollapsed(false)}
              aria-label="Expand study memory"
            >
              +
            </button>
          </aside>
        ) : (
          <aside className="workspace-panel workspace-panel--notes-list">
            <div className="workspace-panel__header">
              <div>
                <p className="workspace-panel__eyebrow">Notebook notes</p>
                <h3>Study memory</h3>
              </div>
              <div className="workspace-panel__header-actions">
                <span className="workspace-kicker">{notes.length} notes</span>
                <button
                  className="workspace-icon-button"
                  type="button"
                  onClick={() => setIsMemoryCollapsed(true)}
                  aria-label="Collapse study memory"
                  aria-expanded
                >
                  -
                </button>
              </div>
            </div>

            <div className="workspace-panel__body workspace-panel__body--notes">
              {loading ? <div className="workspace-empty">Loading notes...</div> : null}
              {!loading && notes.length === 0 ? (
                <div className="workspace-empty">
                  No notes yet. Start a draft and save summaries, study packs, or tutor answers.
                </div>
              ) : null}
              {!loading && notes.length > 0 ? (
                <div className="workspace-note-list">
                  {notes.map((note) => (
                    <div
                      key={note.id}
                      className={`workspace-note-list__item ${note.id === selectedNoteId ? "is-active" : ""}`}
                    >
                      <button
                        className="workspace-note-list__content"
                        type="button"
                        onClick={() => handleSelectNote(note)}
                      >
                        <strong>{note.title}</strong>
                        <span className="workspace-note-list__meta">
                          <span>{note.note_type.replace(/_/g, " ")}</span>
                          <span>{note.converted_source_id ? "Indexed as source" : "Tutor context"}</span>
                        </span>
                      </button>
                      <button
                        className="workspace-icon-button workspace-icon-button--danger"
                        type="button"
                        onClick={() => void handleDeleteNote(note)}
                        disabled={busyNoteId === note.id}
                        aria-label={`Delete note ${note.title}`}
                        title="Delete note"
                      >
                        <TrashIcon />
                      </button>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          </aside>
        )}

        <article className="workspace-panel workspace-panel--note-editor">
          <div className="workspace-panel__header">
            <div>
              <p className="workspace-panel__eyebrow">Editor</p>
              <h3>
                {editorMode === "selected"
                  ? selectedNote?.title ?? "Selected note"
                  : editorMode === "draft"
                    ? "New note"
                    : "Pick a note or start a new draft"}
              </h3>
            </div>
            <span className="workspace-kicker">
              {editorMode === "selected" ? "Edit mode" : editorMode === "draft" ? "Draft mode" : "Idle"}
            </span>
          </div>

          <div className={`workspace-editor ${isIdle ? "workspace-editor--idle" : ""}`}>
            <div className="workspace-editor__titlebar">
              <input
                className="workspace-editor__title"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder={isIdle ? "Select a note or start a draft" : "New note"}
                disabled={!selectedNotebookId || submitting}
                onFocus={activateDraftFromEditor}
              />
              {selectedNote ? (
                <div className="workspace-panel__header-actions">
                  <button
                    className="workspace-icon-button workspace-icon-button--danger"
                    type="button"
                    onClick={() => void handleDeleteNote(selectedNote)}
                    disabled={busyNoteId === selectedNote.id}
                    aria-label="Delete note"
                    title="Delete note"
                  >
                    <TrashIcon />
                  </button>
                  <button
                    className="workspace-icon-button"
                    type="button"
                    onClick={() => handleExport(selectedNote)}
                    disabled={busyNoteId === selectedNote.id}
                    aria-label="Export note to PDF"
                    title="Export note to PDF"
                  >
                    <ToolbarIcon d="M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Zm7 1.5V9h4.5" />
                  </button>
                </div>
              ) : null}
            </div>

            {selectedNote ? (
              <div className="workspace-note-meta">
                <span className="workspace-citation-chip">
                  {selectedNote.visibility.replace(/_/g, " ")}
                </span>
                <span className="workspace-citation-chip">
                  {selectedNote.converted_source_id ? "Indexed as source" : "Used by tutor context"}
                </span>
                <span className="workspace-citation-chip">
                  {selectedNote.note_type.replace(/_/g, " ")}
                </span>
              </div>
            ) : null}

            <div
              className="workspace-editor__toolbar workspace-editor__toolbar--icon"
              onMouseDown={handleToolbarMouseDown}
            >
              <button
                className={`workspace-toolbar-button ${toolbarState.isHeading ? "is-active" : ""}`}
                type="button"
                onClick={() => applyCommand("formatBlock", "<h1>")}
                disabled={!selectedNotebookId || submitting}
                aria-label="Heading"
                title="Heading"
              >
                <ToolbarIcon d="M5 5v14M19 5v14M5 12h14" />
              </button>
              <button
                className={`workspace-toolbar-button ${toolbarState.isBold ? "is-active" : ""}`}
                type="button"
                onClick={() => applyCommand("bold")}
                disabled={!selectedNotebookId || submitting}
                aria-label="Bold"
                title="Bold"
              >
                <ToolbarIcon d="M8 5h6a3 3 0 0 1 0 6H8Zm0 6h7a3 3 0 0 1 0 6H8Z" />
              </button>
              <button
                className={`workspace-toolbar-button ${toolbarState.isItalic ? "is-active" : ""}`}
                type="button"
                onClick={() => applyCommand("italic")}
                disabled={!selectedNotebookId || submitting}
                aria-label="Italic"
                title="Italic"
              >
                <ToolbarIcon d="M14 5h-4M13 5l-2 14M13 19H9" />
              </button>
              <button
                className={`workspace-toolbar-button ${toolbarState.isUnderline ? "is-active" : ""}`}
                type="button"
                onClick={() => applyCommand("underline")}
                disabled={!selectedNotebookId || submitting}
                aria-label="Underline"
                title="Underline"
              >
                <ToolbarIcon d="M7 5v6a5 5 0 0 0 10 0V5M5 19h14" />
              </button>
              <button
                className={`workspace-toolbar-button ${toolbarState.isList ? "is-active" : ""}`}
                type="button"
                onClick={() => applyCommand("insertUnorderedList")}
                disabled={!selectedNotebookId || submitting}
                aria-label="Bulleted list"
                title="Bulleted list"
              >
                <ToolbarIcon d="M8 7h11M8 12h11M8 17h11M4.5 7h.01M4.5 12h.01M4.5 17h.01" />
              </button>
              <button
                className={`workspace-toolbar-button ${toolbarState.isQuote ? "is-active" : ""}`}
                type="button"
                onClick={() => applyCommand("formatBlock", "<blockquote>")}
                disabled={!selectedNotebookId || submitting}
                aria-label="Quote"
                title="Quote"
              >
                <ToolbarIcon d="M7 8h4v4H8v4H5v-4l2-4Zm8 0h4v4h-3v4h-3v-4l2-4Z" />
              </button>
              <button
                className={`workspace-toolbar-button workspace-toolbar-button--swatch ${
                  toolbarState.activeHighlight === "#fff59d" ? "is-active" : ""
                }`}
                type="button"
                onClick={() => applyHighlight("#fff59d")}
                disabled={!selectedNotebookId || submitting}
                aria-label="Highlight yellow"
                title="Highlight yellow"
              >
                <span style={{ backgroundColor: "#fff59d" }} />
              </button>
              <button
                className={`workspace-toolbar-button workspace-toolbar-button--swatch ${
                  toolbarState.activeHighlight === "#b9f6ca" ? "is-active" : ""
                }`}
                type="button"
                onClick={() => applyHighlight("#b9f6ca")}
                disabled={!selectedNotebookId || submitting}
                aria-label="Highlight green"
                title="Highlight green"
              >
                <span style={{ backgroundColor: "#b9f6ca" }} />
              </button>
              <button
                className={`workspace-toolbar-button workspace-toolbar-button--swatch ${
                  toolbarState.activeHighlight === "#ffd1dc" ? "is-active" : ""
                }`}
                type="button"
                onClick={() => applyHighlight("#ffd1dc")}
                disabled={!selectedNotebookId || submitting}
                aria-label="Highlight pink"
                title="Highlight pink"
              >
                <span style={{ backgroundColor: "#ffd1dc" }} />
              </button>
              <label className="workspace-font-size" aria-label="Font size">
                <span>A</span>
                <input
                  type="number"
                  min={10}
                  max={48}
                  step={1}
                  value={fontSizeInput}
                  onChange={(event) => handleFontSizeInputChange(event.target.value)}
                  onMouseDown={handleFontSizeMouseDown}
                  onKeyDown={handleFontSizeKeyDown}
                  onBlur={() => {
                    commitFontSizeInput(fontSizeInput);
                  }}
                  disabled={!selectedNotebookId || submitting}
                />
              </label>
            </div>

            <div className="workspace-note-canvas workspace-note-canvas--single">
              <div
                ref={editorRef}
                className="workspace-editor__richtext"
                contentEditable={Boolean(selectedNotebookId) && !submitting}
                suppressContentEditableWarning
                onInput={() => {
                  syncMarkupFromEditor();
                  refreshToolbarState();
                }}
                onFocus={() => {
                  activateDraftFromEditor();
                  refreshToolbarState();
                }}
                onMouseUp={refreshToolbarState}
                onKeyUp={refreshToolbarState}
                data-placeholder={
                  isIdle
                    ? "Click to start writing, or pick a note from Study memory."
                    : "Write your note..."
                }
              />
            </div>

            <div className="workspace-editor__footer">
              <div className="workspace-editor__status">
                {statusMessage ? <span className="workspace-muted">{statusMessage}</span> : null}
                {error ? <p className="workspace-error">{error}</p> : null}
              </div>
              <div className="workspace-editor__actions">
                {selectedNote ? (
                  <button
                    className="workspace-action workspace-action--ghost"
                    type="button"
                    onClick={() => handleConvert(selectedNote.id)}
                    disabled={busyNoteId === selectedNote.id || Boolean(selectedNote.converted_source_id)}
                  >
                    {selectedNote.converted_source_id ? "Converted to source" : "Convert to source"}
                  </button>
                ) : null}
                <Link className="workspace-action workspace-action--ghost" to="/quizzes">
                  Generate quiz
                </Link>
                <button
                  className="workspace-action"
                  type="button"
                  onClick={handleSave}
                  disabled={!selectedNotebookId || submitting || isIdle}
                >
                  {submitting ? "Saving..." : selectedNoteId ? "Save changes" : "Save note"}
                </button>
              </div>
            </div>
          </div>
        </article>
      </div>
    </section>
  );
}
