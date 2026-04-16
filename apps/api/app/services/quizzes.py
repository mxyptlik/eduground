from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.enums import AttemptStatus
from app.models.identity import User
from app.models.learning import Quiz, QuizAttempt, QuizAttemptItem, QuizItem, TopicMastery
from app.policies.rbac import require_notebook_access
from app.repositories.learning import LearningRepository
from app.repositories.notebooks import NotebookRepository
from app.schemas.quizzes import QuizGenerateRequest, QuizAttemptSubmit
from app.services.audit import AuditService
from app.workflows.generate_quiz import GenerateQuizWorkflow


class QuizService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.learning = LearningRepository(db)
        self.notebooks = NotebookRepository(db)
        self.audit = AuditService(db)
        self.workflow = GenerateQuizWorkflow(db)

    def generate(self, notebook_id: str, payload: QuizGenerateRequest, user: User) -> Quiz:
        require_notebook_access(self.notebooks.get_membership(notebook_id, user.id), write=True)
        quiz, items = self.workflow.run(notebook_id=notebook_id, payload=payload, user=user)
        quiz = self.learning.create_quiz(quiz)
        for item in items:
            item.quiz_id = quiz.id
        self.learning.create_quiz_items(items)
        self.audit.record(actor_user_id=user.id, action_type="quiz.generate", resource_type="quiz", resource_id=quiz.id, notebook_id=notebook_id)
        return quiz

    def list_for_notebook(self, notebook_id: str, user: User) -> tuple[list[Quiz], dict[str, list[QuizItem]]]:
        require_notebook_access(self.notebooks.get_membership(notebook_id, user.id))
        quizzes = self.learning.list_quizzes(notebook_id)
        items_by_quiz = {quiz.id: self.learning.list_quiz_items(quiz.id) for quiz in quizzes}
        return quizzes, items_by_quiz

    def get(self, quiz_id: str, user: User) -> tuple[Quiz, list[QuizItem]]:
        quiz = self.learning.get_quiz(quiz_id)
        if quiz is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quiz not found")
        require_notebook_access(self.notebooks.get_membership(quiz.notebook_id, user.id))
        items = self.learning.list_quiz_items(quiz.id)
        return quiz, items

    def create_attempt(self, quiz_id: str, user: User) -> QuizAttempt:
        quiz, _ = self.get(quiz_id, user)
        attempt = QuizAttempt(
            quiz_id=quiz.id,
            user_id=user.id,
            started_at=datetime.now(UTC),
            status=AttemptStatus.IN_PROGRESS,
        )
        attempt = self.learning.create_attempt(attempt)
        self.audit.record(actor_user_id=user.id, action_type="quiz.attempt.create", resource_type="quiz_attempt", resource_id=attempt.id, notebook_id=quiz.notebook_id)
        return attempt

    def delete(self, quiz_id: str, user: User) -> None:
        quiz, items = self.get(quiz_id, user)
        require_notebook_access(self.notebooks.get_membership(quiz.notebook_id, user.id), write=True)
        resource_id = quiz.id
        notebook_id = quiz.notebook_id
        item_count = len(items)
        self.learning.delete_quiz(resource_id)
        self.audit.record(
            actor_user_id=user.id,
            action_type="quiz.delete",
            resource_type="quiz",
            resource_id=resource_id,
            notebook_id=notebook_id,
            metadata={"item_count": item_count},
        )

    def submit_attempt(self, attempt_id: str, payload: QuizAttemptSubmit, user: User) -> QuizAttempt:
        attempt = self.learning.get_attempt(attempt_id)
        if attempt is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attempt not found")
        if attempt.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot submit another user's attempt")
        quiz = self.learning.get_quiz(attempt.quiz_id)
        assert quiz is not None
        require_notebook_access(self.notebooks.get_membership(quiz.notebook_id, user.id))
        items = self.learning.list_quiz_items(quiz.id)
        correct = 0
        results: list[QuizAttemptItem] = []
        answered_at = datetime.now(UTC)
        for item in items:
            submitted = payload.answers.get(item.id, {})
            is_correct = submitted == item.correct_answer_json
            if is_correct:
                correct += 1
            feedback = self._build_attempt_feedback(item=item, submitted=submitted, is_correct=is_correct)
            results.append(
                QuizAttemptItem(
                    quiz_attempt_id=attempt.id,
                    quiz_item_id=item.id,
                    submitted_answer_json=submitted,
                    is_correct=is_correct,
                    feedback_markdown=feedback,
                    earned_points=1.0 if is_correct else 0.0,
                    answered_at=answered_at,
                )
            )
        score_percent = (correct / len(items) * 100) if items else 0.0
        attempt.status = AttemptStatus.SUBMITTED
        attempt.submitted_at = answered_at
        attempt.score_percent = score_percent
        attempt.score_numeric = correct
        saved = self.learning.save_attempt(attempt)
        saved_results = self.learning.create_attempt_items(results)
        mastery = TopicMastery(
            user_id=user.id,
            notebook_id=quiz.notebook_id,
            mastery_score=round(score_percent / 100, 4),
            evidence_count=len(items),
            last_evaluated_at=datetime.now(UTC),
        )
        self.learning.save_mastery(mastery)
        self.audit.record(actor_user_id=user.id, action_type="quiz.attempt.submit", resource_type="quiz_attempt", resource_id=saved.id, notebook_id=quiz.notebook_id, metadata={"score_percent": score_percent})
        total_items = len(items)
        saved.results = [
            SimpleNamespace(
                quiz_item_id=result.quiz_item_id,
                submitted_answer_json=result.submitted_answer_json,
                correct_answer_json=next((item.correct_answer_json for item in items if item.id == result.quiz_item_id), {}),
                is_correct=result.is_correct,
                feedback_markdown=result.feedback_markdown,
            )
            for result in saved_results
        ]
        saved.correct_count = correct
        saved.total_items = total_items
        saved.passed = score_percent >= 70 if total_items else False
        saved.result_comment = self._build_result_comment(score_percent=score_percent, total_items=total_items)
        return saved

    def _build_attempt_feedback(self, *, item: QuizItem, submitted: dict, is_correct: bool) -> str:
        if is_correct:
            return f"Correct. {item.rationale_markdown}"
        submitted_choice = submitted.get("choice") if isinstance(submitted, dict) else None
        correct_choice = item.correct_answer_json.get("choice")
        if submitted_choice:
            return f"Not quite. You chose `{submitted_choice}`. The correct answer is `{correct_choice}`. {item.rationale_markdown}"
        return f"No answer was selected. The correct answer is `{correct_choice}`. {item.rationale_markdown}"

    def _build_result_comment(self, *, score_percent: float, total_items: int) -> str:
        if total_items == 0:
            return "This quiz had no gradable items."
        if score_percent >= 85:
            return "Strong pass. You are moving through this notebook with real command."
        if score_percent >= 70:
            return "Pass. The core ideas are landing, but there is still room to sharpen the edges."
        if score_percent >= 50:
            return "Close, but not there yet. Review the misses, then run it again while the material is still fresh."
        return "This attempt needs another pass. Re-open the notebook, check the evidence, and retry with a slower read."
