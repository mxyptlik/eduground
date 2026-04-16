from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.learning import Note, Quiz, QuizAttempt, QuizAttemptItem, QuizItem, QuizItemCitation, TopicMastery


class LearningRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_note(self, note: Note) -> Note:
        self.db.add(note)
        self.db.commit()
        self.db.refresh(note)
        return note

    def save_note(self, note: Note) -> Note:
        self.db.add(note)
        self.db.commit()
        self.db.refresh(note)
        return note

    def get_note(self, note_id: str) -> Note | None:
        return self.db.get(Note, note_id)

    def list_notes(self, notebook_id: str) -> list[Note]:
        return (
            self.db.query(Note)
            .filter(Note.notebook_id == notebook_id)
            .order_by(Note.updated_at.desc())
            .all()
        )

    def delete_note(self, note_id: str) -> None:
        self.db.query(Note).filter(Note.id == note_id).delete(synchronize_session=False)
        self.db.commit()

    def create_quiz(self, quiz: Quiz) -> Quiz:
        self.db.add(quiz)
        self.db.commit()
        self.db.refresh(quiz)
        return quiz

    def create_quiz_items(self, items: list[QuizItem]) -> list[QuizItem]:
        self.db.add_all(items)
        self.db.commit()
        for item in items:
            self.db.refresh(item)
        return items

    def get_quiz(self, quiz_id: str) -> Quiz | None:
        return self.db.get(Quiz, quiz_id)

    def list_quizzes(self, notebook_id: str) -> list[Quiz]:
        return (
            self.db.query(Quiz)
            .filter(Quiz.notebook_id == notebook_id)
            .order_by(Quiz.created_at.desc())
            .all()
        )

    def list_quiz_items(self, quiz_id: str) -> list[QuizItem]:
        return (
            self.db.query(QuizItem)
            .filter(QuizItem.quiz_id == quiz_id)
            .order_by(QuizItem.position.asc())
            .all()
        )

    def create_attempt(self, attempt: QuizAttempt) -> QuizAttempt:
        self.db.add(attempt)
        self.db.commit()
        self.db.refresh(attempt)
        return attempt

    def get_attempt(self, attempt_id: str) -> QuizAttempt | None:
        return self.db.get(QuizAttempt, attempt_id)

    def save_attempt(self, attempt: QuizAttempt) -> QuizAttempt:
        self.db.add(attempt)
        self.db.commit()
        self.db.refresh(attempt)
        return attempt

    def create_attempt_items(self, items: list[QuizAttemptItem]) -> list[QuizAttemptItem]:
        self.db.add_all(items)
        self.db.commit()
        for item in items:
            self.db.refresh(item)
        return items

    def list_attempt_items(self, attempt_id: str) -> list[QuizAttemptItem]:
        return (
            self.db.query(QuizAttemptItem)
            .filter(QuizAttemptItem.quiz_attempt_id == attempt_id)
            .order_by(QuizAttemptItem.answered_at.asc())
            .all()
        )

    def delete_quiz(self, quiz_id: str) -> None:
        quiz_item_ids = [
            row[0]
            for row in self.db.query(QuizItem.id).filter(QuizItem.quiz_id == quiz_id).all()
        ]
        attempt_ids = [
            row[0]
            for row in self.db.query(QuizAttempt.id).filter(QuizAttempt.quiz_id == quiz_id).all()
        ]

        if attempt_ids:
            (
                self.db.query(QuizAttemptItem)
                .filter(QuizAttemptItem.quiz_attempt_id.in_(attempt_ids))
                .delete(synchronize_session=False)
            )
            (
                self.db.query(QuizAttempt)
                .filter(QuizAttempt.id.in_(attempt_ids))
                .delete(synchronize_session=False)
            )

        if quiz_item_ids:
            (
                self.db.query(QuizItemCitation)
                .filter(QuizItemCitation.quiz_item_id.in_(quiz_item_ids))
                .delete(synchronize_session=False)
            )
            (
                self.db.query(QuizItem)
                .filter(QuizItem.id.in_(quiz_item_ids))
                .delete(synchronize_session=False)
            )

        self.db.query(Quiz).filter(Quiz.id == quiz_id).delete(synchronize_session=False)
        self.db.commit()

    def save_mastery(self, mastery: TopicMastery) -> TopicMastery:
        self.db.add(mastery)
        self.db.commit()
        self.db.refresh(mastery)
        return mastery
