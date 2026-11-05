from __future__ import annotations
from contextlib import contextmanager

from sqlalchemy.orm import Session
from app.models.enums import ChatRole, MessageStatus

from app.models.tutoring import AssistantAnswer, ChatMessage, ChatSession, Citation, RetrievalTrace, RetrievalTraceItem



class ChatRepository:
    def __init__(self, db: Session) -> None:
        self.db = db
        self._atomic = False

    @contextmanager
    def atomic_answer(self):
        """Commit the complete answer graph once; rollback every row on failure."""
        self._atomic = True
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        finally:
            self._atomic = False

    def _persist(self, rows):
        self.db.add_all(rows)
        if self._atomic:
            self.db.flush()
        else:
            self.db.commit()
            for row in rows:
                self.db.refresh(row)

    def create_session(self, session: ChatSession) -> ChatSession:
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def get_session(self, session_id: str) -> ChatSession | None:
        return self.db.get(ChatSession, session_id)

    def get_message(self, message_id: str) -> ChatMessage | None:
        return self.db.get(ChatMessage, message_id)

    def create_message(self, message: ChatMessage) -> ChatMessage:
        self._persist([message])
        return message

    def save_message(self, message: ChatMessage) -> ChatMessage:
        self._persist([message])
        return message

    def list_messages_for_session(self, session_id: str) -> list[ChatMessage]:
        return (
            self.db.query(ChatMessage)
            .filter(ChatMessage.chat_session_id == session_id)
            .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            .all()
        )

    def save_answer(self, answer: AssistantAnswer) -> AssistantAnswer:
        self._persist([answer])
        return answer

    def list_recent_messages_for_session(self, session_id: str, limit: int) -> list[ChatMessage]:
        rows = (self.db.query(ChatMessage)
                .filter(ChatMessage.chat_session_id == session_id,
                        ChatMessage.role.in_([ChatRole.USER, ChatRole.ASSISTANT]),
                        ChatMessage.status == MessageStatus.COMPLETE)
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                .limit(limit).all())
        return list(reversed(rows))

    def save_trace(self, trace: RetrievalTrace) -> RetrievalTrace:
        self._persist([trace])
        return trace

    def save_trace_items(self, items: list[RetrievalTraceItem]) -> list[RetrievalTraceItem]:
        self._persist(items)
        return items

    def save_citations(self, citations: list[Citation]) -> list[Citation]:
        self._persist(citations)
        return citations

    def get_citations_for_message(self, message_id: str) -> list[Citation]:
        return (
            self.db.query(Citation)
            .join(AssistantAnswer, Citation.assistant_answer_id == AssistantAnswer.id)
            .filter(AssistantAnswer.chat_message_id == message_id)
            .all()
        )
