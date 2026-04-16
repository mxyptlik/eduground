from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.tutoring import AssistantAnswer, ChatMessage, ChatSession, Citation, RetrievalTrace, RetrievalTraceItem



class ChatRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

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
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message

    def save_message(self, message: ChatMessage) -> ChatMessage:
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message

    def list_messages_for_session(self, session_id: str) -> list[ChatMessage]:
        return (
            self.db.query(ChatMessage)
            .filter(ChatMessage.chat_session_id == session_id)
            .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            .all()
        )

    def save_answer(self, answer: AssistantAnswer) -> AssistantAnswer:
        self.db.add(answer)
        self.db.commit()
        self.db.refresh(answer)
        return answer

    def save_trace(self, trace: RetrievalTrace) -> RetrievalTrace:
        self.db.add(trace)
        self.db.commit()
        self.db.refresh(trace)
        return trace

    def save_trace_items(self, items: list[RetrievalTraceItem]) -> list[RetrievalTraceItem]:
        self.db.add_all(items)
        self.db.commit()
        for item in items:
            self.db.refresh(item)
        return items

    def save_citations(self, citations: list[Citation]) -> list[Citation]:
        self.db.add_all(citations)
        self.db.commit()
        for citation in citations:
            self.db.refresh(citation)
        return citations

    def get_citations_for_message(self, message_id: str) -> list[Citation]:
        return (
            self.db.query(Citation)
            .join(AssistantAnswer, Citation.assistant_answer_id == AssistantAnswer.id)
            .filter(AssistantAnswer.chat_message_id == message_id)
            .all()
        )
