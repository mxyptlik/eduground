from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import DBSession, get_current_user
from app.schemas.chat import AnswerResponse, ChatMessageCreate, ChatSessionCreate, ChatSessionResponse, CitationResponse
from app.services.chat import ChatService

router = APIRouter()


@router.post("/notebooks/{notebook_id}/chat/sessions", response_model=ChatSessionResponse)
def create_chat_session(notebook_id: UUID, payload: ChatSessionCreate, db: DBSession, user=Depends(get_current_user)) -> ChatSessionResponse:
    return ChatSessionResponse.model_validate(ChatService(db).create_session(str(notebook_id), payload.title, user))


@router.get("/chat/sessions/{session_id}", response_model=ChatSessionResponse)
def get_chat_session(session_id: UUID, db: DBSession, user=Depends(get_current_user)) -> ChatSessionResponse:
    return ChatSessionResponse.model_validate(ChatService(db).get_session(str(session_id), user))


@router.post("/chat/sessions/{session_id}/messages", response_model=AnswerResponse)
def post_chat_message(session_id: UUID, payload: ChatMessageCreate, db: DBSession, user=Depends(get_current_user)) -> AnswerResponse:
    _, answer = ChatService(db).post_message(
        str(session_id),
        payload.content_markdown,
        selected_source_ids=payload.selected_source_ids,
        selected_module_ids=payload.selected_module_ids,
        user=user,
    )
    return answer


@router.post("/chat/sessions/{session_id}/messages/stream")
def stream_chat_message(session_id: UUID, payload: ChatMessageCreate, db: DBSession, user=Depends(get_current_user)) -> StreamingResponse:
    events = ChatService(db).stream_message(
        str(session_id),
        payload.content_markdown,
        selected_source_ids=payload.selected_source_ids,
        selected_module_ids=payload.selected_module_ids,
        user=user,
    )

    def event_stream():
        try:
            for event in events:
                yield json.dumps(event, ensure_ascii=True) + "\n"
        except Exception as exc:
            yield json.dumps({"type": "error", "data": {"message": str(exc)}}, ensure_ascii=True) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@router.get("/chat/messages/{message_id}/citations", response_model=list[CitationResponse])
def get_chat_message_citations(message_id: UUID, db: DBSession, user=Depends(get_current_user)) -> list[CitationResponse]:
    citations = ChatService(db).get_citations(str(message_id), user)
    return [CitationResponse.model_validate(citation) for citation in citations]
