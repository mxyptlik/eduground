from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.deps import get_current_user
from app.api.routes import notebooks as notebook_routes
from app.db.session import get_db_session
from app.models.enums import NotebookVisibility, PolicyMode

pytestmark = pytest.mark.module


def test_list_notebooks_returns_marshaled_payload(client, override_dependency, monkeypatch) -> None:
    override_dependency(get_current_user, lambda: SimpleNamespace(id="user-1"))
    override_dependency(get_db_session, lambda: None)

    class FakeNotebookService:
        def __init__(self, db) -> None:
            self.db = db

        def list_for_user(self, user):
            assert user.id == "user-1"
            return [
                SimpleNamespace(
                    id="21431eb1-a026-4267-ac90-35dbbcb8061f",
                    institution_id="3eaf424f-d1a7-4812-b3ef-d2fe32f2f94a",
                    title="Biology 101",
                    description="Intro notebook",
                    owner_user_id="user-1",
                    visibility=NotebookVisibility.PRIVATE,
                    policy_mode=PolicyMode.TEACHING,
                    course_offering_id=None,
                    source_count=3,
                    learner_count=12,
                )
            ]

    monkeypatch.setattr(notebook_routes, "NotebookService", FakeNotebookService)

    response = client.get("/api/notebooks")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "21431eb1-a026-4267-ac90-35dbbcb8061f",
            "institution_id": "3eaf424f-d1a7-4812-b3ef-d2fe32f2f94a",
            "title": "Biology 101",
            "description": "Intro notebook",
            "owner_user_id": "user-1",
            "visibility": "private",
            "policy_mode": "teaching",
            "course_offering_id": None,
            "source_count": 3,
            "learner_count": 12,
        }
    ]


def test_create_notebook_validates_payload_and_returns_response(client, override_dependency, monkeypatch) -> None:
    override_dependency(get_current_user, lambda: SimpleNamespace(id="user-1"))
    override_dependency(get_db_session, lambda: None)

    class FakeNotebookService:
        def __init__(self, db) -> None:
            self.db = db

        def create(self, payload, user):
            assert payload.title == "Operating Systems"
            assert user.id == "user-1"
            return SimpleNamespace(
                id="e6784e19-a4fe-4554-963c-c710c5f41fd1",
                institution_id="3eaf424f-d1a7-4812-b3ef-d2fe32f2f94a",
                title=payload.title,
                description=payload.description,
                owner_user_id=user.id,
                visibility=payload.visibility,
                policy_mode=payload.policy_mode,
                course_offering_id=payload.course_offering_id,
            )

    monkeypatch.setattr(notebook_routes, "NotebookService", FakeNotebookService)

    response = client.post(
        "/api/notebooks",
        json={
            "title": "Operating Systems",
            "description": "Kernel and process isolation",
            "visibility": "private",
            "policy_mode": "teaching",
            "course_offering_id": None,
        },
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Operating Systems"
    assert response.json()["owner_user_id"] == "user-1"


def test_get_notebook_rejects_invalid_uuid(client, override_dependency) -> None:
    override_dependency(get_current_user, lambda: SimpleNamespace(id="user-1"))

    response = client.get("/api/notebooks/not-a-uuid")

    assert response.status_code == 422
