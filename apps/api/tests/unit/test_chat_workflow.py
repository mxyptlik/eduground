from __future__ import annotations

from types import SimpleNamespace

from app.workflows.answer_question import AnswerQuestionWorkflow, CandidateRecord, NoteContextRecord


def test_build_citation_preview_uses_chunk_and_source_metadata():
    workflow = AnswerQuestionWorkflow.__new__(AnswerQuestionWorkflow)
    candidate = CandidateRecord(
        chunk=SimpleNamespace(id="chunk-1", text="Quoted text" * 40, start_page=2, end_page=3, start_slide=None, end_slide=None),
        source=SimpleNamespace(id="source-1", title="Biology Notes"),
        score=0.9,
        rank_before=1,
        rank_after=1,
        rerank_score=None,
    )

    preview = workflow.build_citation_preview(candidate)

    assert preview["source_id"] == "source-1"
    assert preview["chunk_id"] == "chunk-1"
    assert preview["display_label"] == "Biology Notes, page 2"
    assert preview["quote_text"]


def test_build_context_includes_sources_and_notebook_notes():
    workflow = AnswerQuestionWorkflow.__new__(AnswerQuestionWorkflow)
    candidates = [
        CandidateRecord(
            chunk=SimpleNamespace(id="chunk-1", text="Cells divide through mitosis.", start_page=2, end_page=2, start_slide=None, end_slide=None),
            source=SimpleNamespace(id="source-1", title="Biology Notes"),
            score=0.9,
            rank_before=1,
            rank_after=1,
            rerank_score=None,
        )
    ]
    note_contexts = [
        NoteContextRecord(
            note_id="note-1",
            title="Mitosis reminder",
            content_markdown="Mitosis happens before cytokinesis.",
            score=2.0,
        )
    ]

    context = workflow._build_context(candidates, note_contexts)

    assert "Cells divide through mitosis." in context
    assert "Notebook note: Mitosis reminder" in context
    assert "Mitosis happens before cytokinesis." in context


def test_score_note_match_requires_overlap():
    workflow = AnswerQuestionWorkflow.__new__(AnswerQuestionWorkflow)

    matching_score = workflow._score_note_match(
        "Cell division is mitosis.",
        {"cell", "mitosis"},
        "cell mitosis",
    )
    missing_score = workflow._score_note_match(
        "Photosynthesis happens in chloroplasts.",
        {"cell", "mitosis"},
        "cell mitosis",
    )

    assert matching_score > 0
    assert missing_score == 0
