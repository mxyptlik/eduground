from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.deps import DBSession, get_current_user
from app.schemas.notes import NoteCreate, NoteExportResponse, NoteResponse, NoteUpdate
from app.services.notes import NoteService

router = APIRouter()


@router.get("/notebooks/{notebook_id}/notes", response_model=list[NoteResponse])
def list_notes(notebook_id: UUID, db: DBSession, user=Depends(get_current_user)) -> list[NoteResponse]:
    return [NoteResponse.model_validate(note) for note in NoteService(db).list_for_notebook(str(notebook_id), user)]


@router.post("/notebooks/{notebook_id}/notes", response_model=NoteResponse)
def create_note(notebook_id: UUID, payload: NoteCreate, db: DBSession, user=Depends(get_current_user)) -> NoteResponse:
    return NoteResponse.model_validate(NoteService(db).create(str(notebook_id), payload, user))


@router.patch("/notes/{note_id}", response_model=NoteResponse)
def update_note(note_id: UUID, payload: NoteUpdate, db: DBSession, user=Depends(get_current_user)) -> NoteResponse:
    try:
        note = NoteService(db).update(str(note_id), payload, user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return NoteResponse.model_validate(note)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(note_id: UUID, db: DBSession, user=Depends(get_current_user)) -> Response:
    try:
        NoteService(db).delete(str(note_id), user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/notes/{note_id}/convert-to-source")
def convert_note_to_source(note_id: UUID, db: DBSession, user=Depends(get_current_user)) -> dict[str, str]:
    try:
        source = NoteService(db).convert_to_source(str(note_id), user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"note_id": str(note_id), "source_id": source.id, "status": "converted"}


@router.post("/notes/{note_id}/export", response_model=NoteExportResponse)
def export_note(note_id: UUID, db: DBSession, user=Depends(get_current_user)) -> NoteExportResponse:
    try:
        content = NoteService(db).export(str(note_id), user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return NoteExportResponse(note_id=str(note_id), export_format="markdown", content=content)
