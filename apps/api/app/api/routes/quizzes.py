from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import DBSession, get_current_user
from app.schemas.quizzes import QuizAttemptResponse, QuizAttemptSubmit, QuizGenerateRequest, QuizItemResponse, QuizResponse
from app.services.quizzes import QuizService

router = APIRouter()


@router.get("/notebooks/{notebook_id}/quizzes", response_model=list[QuizResponse])
def list_quizzes(notebook_id: UUID, db: DBSession, user=Depends(get_current_user)) -> list[QuizResponse]:
    quizzes, items_by_quiz = QuizService(db).list_for_notebook(str(notebook_id), user)
    return [
        QuizResponse(
            id=quiz.id,
            notebook_id=quiz.notebook_id,
            title=quiz.title,
            difficulty=quiz.difficulty,
            status=quiz.status,
            created_at=quiz.created_at,
            items=[QuizItemResponse.model_validate(item) for item in items_by_quiz[quiz.id]],
        )
        for quiz in quizzes
    ]


@router.post("/notebooks/{notebook_id}/quizzes/generate", response_model=QuizResponse)
def generate_quiz(notebook_id: UUID, payload: QuizGenerateRequest, db: DBSession, user=Depends(get_current_user)) -> QuizResponse:
    service = QuizService(db)
    quiz = service.generate(str(notebook_id), payload, user)
    _, items = service.get(quiz.id, user)
    return QuizResponse(
        id=quiz.id,
        notebook_id=quiz.notebook_id,
        title=quiz.title,
        difficulty=quiz.difficulty,
        status=quiz.status,
        created_at=quiz.created_at,
        items=[QuizItemResponse.model_validate(item) for item in items],
    )


@router.get("/quizzes/{quiz_id}", response_model=QuizResponse)
def get_quiz(quiz_id: UUID, db: DBSession, user=Depends(get_current_user)) -> QuizResponse:
    quiz, items = QuizService(db).get(str(quiz_id), user)
    return QuizResponse(
        id=quiz.id,
        notebook_id=quiz.notebook_id,
        title=quiz.title,
        difficulty=quiz.difficulty,
        status=quiz.status,
        created_at=quiz.created_at,
        items=[QuizItemResponse.model_validate(item) for item in items],
    )


@router.delete("/quizzes/{quiz_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_quiz(quiz_id: UUID, db: DBSession, user=Depends(get_current_user)) -> Response:
    QuizService(db).delete(str(quiz_id), user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/quizzes/{quiz_id}/attempts", response_model=QuizAttemptResponse)
def create_attempt(quiz_id: UUID, db: DBSession, user=Depends(get_current_user)) -> QuizAttemptResponse:
    return QuizAttemptResponse.model_validate(QuizService(db).create_attempt(str(quiz_id), user))


@router.post("/attempts/{attempt_id}/submit", response_model=QuizAttemptResponse)
def submit_attempt(attempt_id: UUID, payload: QuizAttemptSubmit, db: DBSession, user=Depends(get_current_user)) -> QuizAttemptResponse:
    return QuizAttemptResponse.model_validate(QuizService(db).submit_attempt(str(attempt_id), payload, user))
