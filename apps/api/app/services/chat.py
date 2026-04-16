from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.errors import ApiServiceError
from app.models.enums import ChatRole, MessageStatus
from app.models.identity import User
from app.models.tutoring import ChatMessage, ChatSession
from app.policies.rbac import require_notebook_access
from app.repositories.chat import ChatRepository
from app.repositories.notebooks import NotebookRepository
from app.schemas.chat import AnswerResponse
from app.services.audit import AuditService
from app.services.chat_memory import ChatMemoryStore
from app.services.chat_runtime import TutorChatRuntime
from app.workflows.answer_question import AnswerQuestionWorkflow


class ChatService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.chats = ChatRepository(db)
        self.notebooks = NotebookRepository(db)
        self.audit = AuditService(db)
        self.answer_workflow = AnswerQuestionWorkflow(db)
        self.memory_store = ChatMemoryStore(self.chats)
        self.runtime = TutorChatRuntime(self.answer_workflow, self.memory_store)

    def create_session(self, notebook_id: str, title: str | None, user: User) -> ChatSession:
        notebook = self.notebooks.get(notebook_id)
        if notebook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")
        require_notebook_access(self.notebooks.get_membership(notebook_id, user.id))
        session = ChatSession(
            notebook_id=notebook_id,
            user_id=user.id,
            title=title,
            policy_mode_snapshot=notebook.policy_mode,
        )
        session = self.chats.create_session(session)
        self.audit.record(actor_user_id=user.id, action_type="chat.session.create", resource_type="chat_session", resource_id=session.id, notebook_id=notebook_id)
        return session

    def get_session(self, session_id: str, user: User) -> ChatSession:
        session = self.chats.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        require_notebook_access(self.notebooks.get_membership(session.notebook_id, user.id))
        return session

    def post_message(self, session_id: str, content_markdown: str, *, selected_source_ids: list[str], selected_module_ids: list[str], user: User) -> tuple[ChatMessage, AnswerResponse]:
        session = self.get_session(session_id, user)
        message = ChatMessage(
            chat_session_id=session.id,
            role=ChatRole.USER,
            content_markdown=content_markdown,
            status=MessageStatus.COMPLETE,
            created_at=datetime.now(UTC),
        )
        message = self.chats.create_message(message)
        try:
            answer = self.runtime.answer(
                session=session,
                user_message=message,
                selected_source_ids=selected_source_ids,
                selected_module_ids=selected_module_ids,
            )
        except ApiServiceError:
            raise
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        self.audit.record(actor_user_id=user.id, action_type="chat.message.create", resource_type="chat_message", resource_id=message.id, notebook_id=session.notebook_id)
        return message, answer

    def stream_message(
        self,
        session_id: str,
        content_markdown: str,
        *,
        selected_source_ids: list[str],
        selected_module_ids: list[str],
        user: User,
    ):
        session = self.get_session(session_id, user)
        message = ChatMessage(
            chat_session_id=session.id,
            role=ChatRole.USER,
            content_markdown=content_markdown,
            status=MessageStatus.COMPLETE,
            created_at=datetime.now(UTC),
        )
        message = self.chats.create_message(message)
        self.audit.record(actor_user_id=user.id, action_type="chat.message.create", resource_type="chat_message", resource_id=message.id, notebook_id=session.notebook_id)
        return self.runtime.stream_answer(
            session=session,
            user_message=message,
            selected_source_ids=selected_source_ids,
            selected_module_ids=selected_module_ids,
        )

    def get_citations(self, message_id: str, user: User):
        message = self.chats.get_message(message_id)
        if message is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
        session = self.chats.get_session(message.chat_session_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        require_notebook_access(self.notebooks.get_membership(session.notebook_id, user.id))
        return self.chats.get_citations_for_message(message_id)
