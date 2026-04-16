from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import DBSession, RequestAuthContext, get_auth_context
from app.core.security import WebhookVerificationError, verify_svix_webhook
from app.schemas.auth import AuthUser, ClerkWebhookResponse
from app.services.auth import AuthService

router = APIRouter()


@router.get("/auth/me", response_model=AuthUser)
def me(context: RequestAuthContext = Depends(get_auth_context)) -> AuthUser:
    return context.auth_user


@router.post("/webhooks/clerk", response_model=ClerkWebhookResponse, status_code=status.HTTP_202_ACCEPTED)
async def clerk_webhook(request: Request, db: DBSession) -> ClerkWebhookResponse:
    payload = await request.body()
    try:
        verify_svix_webhook(payload=payload, headers=request.headers)
    except WebhookVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    try:
        event = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Clerk webhook payload") from exc

    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Clerk webhook payload missing event type")

    AuthService(db).handle_clerk_webhook(event_type, event)
    return ClerkWebhookResponse(status="accepted", processed_event_type=event_type)
