from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.errors import ApiServiceError
from app.models.enums import PolicyMode, QuizDifficulty, QuizGenerationMode, QuizItemType, QuizStatus
from app.workflows.answer_question import CandidateRecord
from app.workflows.generate_quiz import GenerateQuizWorkflow

pytestmark = pytest.mark.unit


def test_generate_quiz_workflow_returns_grounded_mcq_items(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = GenerateQuizWorkflow(db=SimpleNamespace(get=lambda *_args, **_kwargs: SimpleNamespace(policy_mode=PolicyMode.TEACHING)))
    workflow.llm = SimpleNamespace(
        generate_messages=lambda _messages: """
        {
          "items": [
            {
              "prompt": "Which store is used for semantic retrieval in the backend?",
              "options": ["Qdrant", "PostgreSQL", "Redis", "Cloudinary"],
              "correct_answer": "Qdrant",
              "rationale": "The evidence says embeddings are stored in Qdrant for semantic retrieval."
            },
            {
              "prompt": "Which endpoint handles uploads?",
              "options": ["/api/upload", "/api/files", "/api/ingest", "/api/documents"],
              "correct_answer": "/api/upload",
              "rationale": "The retrieved source explicitly names POST /api/upload."
            }
          ]
        }
        """,
    )

    prepared = SimpleNamespace(
        system_prompt="Teach clearly.",
        context="Qdrant stores embeddings.",
        candidates=[
            CandidateRecord(
                chunk=SimpleNamespace(
                    id="chunk-1",
                    text="Document chunks are embedded and stored in Qdrant for semantic retrieval. Uploads happen at POST /api/upload.",
                    start_page=2,
                    end_page=2,
                    start_slide=None,
                    end_slide=None,
                ),
                source=SimpleNamespace(id="source-1", title="Money Quest Backend"),
                score=0.9,
                rank_before=1,
                rank_after=1,
                rerank_score=None,
            )
        ],
        note_contexts=[],
    )

    monkeypatch.setattr(
        workflow,
        "_build_answer_workflow",
        lambda: SimpleNamespace(prepare_grounded_answer=lambda **_kwargs: prepared),
    )

    quiz, items = workflow.run(
        notebook_id="notebook-1",
        payload=SimpleNamespace(
            title="Backend Features Quiz",
            difficulty=QuizDifficulty.MIXED,
            source_ids=[],
            note_ids=[],
            module_ids=[],
            item_count=2,
        ),
        user=SimpleNamespace(id="user-1"),
    )

    assert quiz.title == "Backend Features Quiz"
    assert quiz.generation_mode == QuizGenerationMode.TUTOR_GENERATED
    assert quiz.status == QuizStatus.DRAFT
    assert len(items) == 2
    assert all(item.item_type == QuizItemType.MCQ for item in items)
    assert items[0].options_json == ["Qdrant", "PostgreSQL", "Redis", "Cloudinary"]
    assert items[0].correct_answer_json == {"choice": "Qdrant"}


def test_generate_quiz_workflow_raises_when_no_grounded_context_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = GenerateQuizWorkflow(db=SimpleNamespace(get=lambda *_args, **_kwargs: SimpleNamespace(policy_mode=PolicyMode.TEACHING)))
    monkeypatch.setattr(
        workflow,
        "_build_answer_workflow",
        lambda: SimpleNamespace(
            prepare_grounded_answer=lambda **_kwargs: SimpleNamespace(
                system_prompt="Teach clearly.",
                context="",
                candidates=[],
                note_contexts=[],
            )
        ),
    )

    with pytest.raises(ApiServiceError) as exc_info:
        workflow.run(
            notebook_id="notebook-1",
            payload=SimpleNamespace(title="Quiz", difficulty=QuizDifficulty.MIXED, source_ids=[], note_ids=[], module_ids=[], item_count=3),
            user=SimpleNamespace(id="user-1"),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "quiz_generation_no_context"


def test_generate_quiz_workflow_uses_selected_notes_without_vector_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    note = SimpleNamespace(
        id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        notebook_id="notebook-1",
        author_user_id="user-1",
        title="Exam reminder",
        content_markdown="Qdrant is the vector database used for semantic retrieval.",
        visibility="private",
    )
    fake_db = SimpleNamespace(
        get=lambda *_args, **_kwargs: SimpleNamespace(policy_mode=PolicyMode.TEACHING),
        query=lambda *_args, **_kwargs: _FakeNoteQuery([note]),
    )
    workflow = GenerateQuizWorkflow(db=fake_db)
    workflow.llm = SimpleNamespace(
        generate_messages=lambda _messages: """
        {"items":[{"prompt":"Which vector database is used?","options":["Qdrant","Redis","SQLite","Cloudinary"],"correct_answer":"Qdrant","rationale":"The selected note states that Qdrant is used."}]}
        """,
    )

    monkeypatch.setattr(
        workflow,
        "_build_answer_workflow",
        lambda: SimpleNamespace(prepare_grounded_answer=lambda **_kwargs: pytest.fail("Should not retrieve vector context for note-only scope")),
    )

    quiz, items = workflow.run(
        notebook_id="notebook-1",
        payload=SimpleNamespace(
            title="Note quiz",
            difficulty=QuizDifficulty.MIXED,
            source_ids=[],
            note_ids=["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"],
            module_ids=[],
            item_count=1,
        ),
        user=SimpleNamespace(id="user-1"),
    )

    assert quiz.scope_json["note_ids"] == ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"]
    assert items[0].correct_answer_json == {"choice": "Qdrant"}


def test_generate_quiz_workflow_retries_when_first_generation_is_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = GenerateQuizWorkflow(db=SimpleNamespace(get=lambda *_args, **_kwargs: SimpleNamespace(policy_mode=PolicyMode.TEACHING)))
    responses = iter(
        [
            '{"items":[{"prompt":"Bad item","options":["Same","Same","Same","Same"],"correct_answer":"Same","rationale":"Bad."}]}',
            '{"items":[{"prompt":"Which vector store is used?","options":["Qdrant","Redis","SQLite","Cloudinary"],"correct_answer":"Qdrant","rationale":"The evidence names Qdrant."}]}',
        ]
    )
    workflow.llm = SimpleNamespace(generate_messages=lambda _messages: next(responses))
    prepared = SimpleNamespace(
        system_prompt="Teach clearly.",
        context="Qdrant stores embeddings.",
        candidates=[
            CandidateRecord(
                chunk=SimpleNamespace(
                    id="chunk-1",
                    text="Document chunks are embedded and stored in Qdrant for semantic retrieval.",
                    start_page=2,
                    end_page=2,
                    start_slide=None,
                    end_slide=None,
                ),
                source=SimpleNamespace(id="source-1", title="Money Quest Backend"),
                score=0.9,
                rank_before=1,
                rank_after=1,
                rerank_score=None,
            )
        ],
        note_contexts=[],
    )

    monkeypatch.setattr(
        workflow,
        "_build_answer_workflow",
        lambda: SimpleNamespace(prepare_grounded_answer=lambda **_kwargs: prepared),
    )

    quiz, items = workflow.run(
        notebook_id="notebook-1",
        payload=SimpleNamespace(
            title="Retry quiz",
            difficulty=QuizDifficulty.MIXED,
            source_ids=[],
            note_ids=[],
            module_ids=[],
            item_count=1,
        ),
        user=SimpleNamespace(id="user-1"),
    )

    assert quiz.title == "Retry quiz"
    assert len(items) == 1
    assert items[0].prompt_text == "Which vector store is used?"


class _FakeNoteQuery:
    def __init__(self, notes) -> None:
        self._notes = notes

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return self._notes
