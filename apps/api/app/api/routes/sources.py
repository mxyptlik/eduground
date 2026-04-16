from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from app.api.deps import DBSession, get_current_user
from app.core.config import settings
from app.core.errors import ProviderRequestError
from app.integrations.storage.factory import get_local_storage_for_signed_routes
from app.schemas.sources import IngestionJobResponse, SourceCreate, SourceResponse, SourceReindexRequest, UploadUrlRequest, UploadUrlResponse
from app.services.sources import SourceService

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
def list_sources(notebook_id: UUID, db: DBSession, user=Depends(get_current_user)) -> list[SourceResponse]:
    return [SourceResponse.model_validate(source) for source in SourceService(db).list_sources(str(notebook_id), user)]


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
