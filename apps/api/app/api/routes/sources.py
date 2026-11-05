from __future__ import annotations
import hashlib
import json

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status

from app.api.deps import DBSession, get_current_user
from app.core.config import settings
from app.core.errors import ProviderRequestError
from app.integrations.storage.factory import get_local_storage_for_signed_routes
from app.schemas.sources import IngestionJobResponse, SourceCreate, SourceResponse, SourceReindexRequest, UploadUrlRequest, UploadUrlResponse
from app.services.sources import SourceService
from app.models.content import Source
from app.policies.rbac import require_notebook_access
from app.repositories.notebooks import NotebookRepository
from app.repositories.sources import SourceRepository

router = APIRouter()


@router.put("/storage/local-upload", status_code=status.HTTP_200_OK)
async def local_storage_upload(
    request: Request,
    token: str,
    content_type: str | None = Header(default=None, alias="Content-Type"),
) -> Response:
    storage = get_local_storage_for_signed_routes()
    signed_request = storage.resolve_upload_token(token)
    payload = await request.body()
    if len(payload) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "code": "payload_too_large",
                "message": f"Upload body exceeds limit of {settings.max_upload_bytes} bytes",
                "retryable": False,
            },
        )
    expected_mime = signed_request.mime_type or "application/octet-stream"
    if content_type and content_type != expected_mime:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_content_type",
                "message": f"Expected Content-Type '{expected_mime}' for this signed upload",
                "retryable": False,
            },
        )
    storage.put_object_bytes(
        signed_request.storage_key,
        payload,
        content_type=content_type or signed_request.mime_type or "application/octet-stream",
    )
    return Response(status_code=status.HTTP_200_OK)


@router.get("/storage/local-download", status_code=status.HTTP_200_OK)
def local_storage_download(token: str) -> Response:
    storage = get_local_storage_for_signed_routes()
    signed_request = storage.resolve_download_token(token)
    try:
        payload = storage.get_object_bytes(signed_request.storage_key)
    except ProviderRequestError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
                "provider": exc.provider,
                "details": exc.details,
            },
        ) from exc
    media_type = signed_request.mime_type or "application/octet-stream"
    return Response(content=payload, media_type=media_type, status_code=status.HTTP_200_OK)


@router.post("/notebooks/{notebook_id}/sources/upload-url", response_model=UploadUrlResponse)
def create_upload_url(notebook_id: UUID, payload: UploadUrlRequest, db: DBSession, user=Depends(get_current_user)) -> UploadUrlResponse:
    return SourceService(db).create_upload_url(str(notebook_id), payload, user)


@router.post("/notebooks/{notebook_id}/sources", response_model=SourceResponse)
def create_source(notebook_id: UUID, payload: SourceCreate, db: DBSession, user=Depends(get_current_user)) -> SourceResponse:
    return SourceResponse.model_validate(SourceService(db).create_source(str(notebook_id), payload, user))


@router.get("/notebooks/{notebook_id}/sources", response_model=list[SourceResponse])
def list_sources(notebook_id: UUID, db: DBSession, limit: int = Query(100, ge=1, le=200),
                 offset: int = Query(0, ge=0), user=Depends(get_current_user)) -> list[SourceResponse]:
    return [SourceResponse.model_validate(source) for source in SourceService(db).list_sources(
        str(notebook_id), user, limit=limit, offset=offset)]


@router.get("/notebooks/{notebook_id}/sources/revision")
def source_revision(notebook_id: UUID, db: DBSession, user=Depends(get_current_user)) -> dict[str, str]:
    scoped_id = str(notebook_id)
    require_notebook_access(NotebookRepository(db).get_membership(scoped_id, user.id))
    digest = hashlib.sha256()
    source_ids = []
    rows = (db.query(Source.id, Source.status, Source.version_number, Source.updated_at)
            .filter(Source.notebook_id == scoped_id).order_by(Source.id).yield_per(100))
    for row in rows:
        source_ids.append(row.id)
        digest.update(json.dumps([row.id, row.status, row.version_number, row.updated_at.isoformat()]).encode())
    repository = SourceRepository(db)
    jobs = {}
    for offset in range(0, len(source_ids), 500):
        jobs.update(repository.list_latest_jobs_for_sources(source_ids[offset:offset + 500]))
    for source_id in sorted(jobs):
        job = jobs[source_id]
        digest.update(json.dumps([source_id, job.id, job.status, job.stage,
                                  job.chunk_count, job.embedded_count, job.indexed_count]).encode())
    return {"revision": digest.hexdigest()}


@router.get("/sources/{source_id}", response_model=SourceResponse)
def get_source(source_id: UUID, db: DBSession, user=Depends(get_current_user)) -> SourceResponse:
    return SourceResponse.model_validate(SourceService(db).get_source(str(source_id), user))


@router.get("/sources/{source_id}/events", response_model=list[IngestionJobResponse])
def list_source_events(source_id: UUID, db: DBSession, user=Depends(get_current_user)) -> list[IngestionJobResponse]:
    return [IngestionJobResponse.model_validate(job) for job in SourceService(db).list_jobs(str(source_id), user)]


@router.delete("/sources/{source_id}", response_model=SourceResponse)
def delete_source(source_id: UUID, db: DBSession, user=Depends(get_current_user)) -> SourceResponse:
    return SourceResponse.model_validate(SourceService(db).delete_source(str(source_id), user))


@router.post("/sources/{source_id}/reindex", response_model=IngestionJobResponse)
def reindex_source(source_id: UUID, payload: SourceReindexRequest, db: DBSession, user=Depends(get_current_user)) -> IngestionJobResponse:
    return IngestionJobResponse.model_validate(SourceService(db).reindex_source(str(source_id), user))
