from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.deps import get_current_user
from app.api.routes import chat as chat_routes
from app.db.session import get_db_session

pytestmark = pytest.mark.module


def test_stream_chat_message_returns_ndjson_events(client, override_dependency, monkeypatch) -> None:
    override_dependency(get_current_user, lambda: SimpleNamespace(id="user-1"))
    override_dependency(get_db_session, lambda: None)

    class FakeChatService:
        def __init__(self, db) -> None:
            self.db = db

        def stream_message(self, session_id: str, content_markdown: str, *, selected_source_ids, selected_module_ids, user):
            assert session_id == "21431eb1-a026-4267-ac90-35dbbcb8061f"
            assert content_markdown == "What is mitosis?"
            assert user.id == "user-1"
            yield {"type": "status", "data": {"phase": "retrieving", "message": "Retrieving sources and notebook notes..."}}
            yield {"type": "sources", "data": [{"source_id": "source-1", "chunk_id": "chunk-1", "display_label": "Biology Notes, page 2", "quote_text": "Cell division", "page_start": 2, "page_end": 2, "slide_start": None, "slide_end": None}]}
            yield {
                "type": "final",
                "data": {
                    "answer_type": "grounded_answer",
                    "content_markdown": "Mitosis is cell division.",
                    "citations": [],
                    "refusal_reason": None,
                    "retrieval_trace": None,
                },
            }

    monkeypatch.setattr(chat_routes, "ChatService", FakeChatService)

    response = client.post(
        "/api/chat/sessions/21431eb1-a026-4267-ac90-35dbbcb8061f/messages/stream",
        json={"content_markdown": "What is mitosis?", "selected_source_ids": [], "selected_module_ids": []},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    lines = [line for line in response.text.splitlines() if line.strip()]
    assert '"type": "status"' in lines[0]
    assert '"type": "sources"' in lines[1]
    assert '"type": "final"' in lines[2]
