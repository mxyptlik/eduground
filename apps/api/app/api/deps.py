from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.observability import counter
from app.core.security import get_clerk_token_verifier
from app.db.session import get_db_session
from app.integrations.auth.clerk import ClerkAuthenticationError, ClerkManagementError, ClerkSession
from app.models.identity import InstitutionMembership, User
from app.schemas.auth import AuthUser
from app.services.auth import AuthService

DBSession = Annotated[Session, Depends(get_db_session)]
logger = get_logger("app.auth")


@dataclass(slots=True)
class RequestAuthContext:
    session: ClerkSession
    user: User
    membership: InstitutionMembership | None
    active_organization_id: str | None
    auth_user: AuthUser


def get_auth_context(
    request: Request,
    db: DBSession,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    active_organization_header: Annotated[str | None, Header(alias=settings.clerk_active_organization_header)] = None,
) -> RequestAuthContext:
    if not settings.clerk_enabled:
        counter(
            "eduground_auth_failures_total",
            labels={"reason": "clerk_disabled"},
            description="Authentication failures by reason",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "clerk_not_configured",
                "message": "Clerk authentication is not enabled for this deployment",
                "retryable": False,
                "provider": "clerk",
            },
        )

    token: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", maxsplit=1)[1]

    if not token:
        counter(
            "eduground_auth_failures_total",
            labels={"reason": "missing_bearer"},
            description="Authentication failures by reason",
        )
        logger.warning("Missing Clerk bearer token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "missing_bearer_token", "message": "Missing Clerk bearer token", "retryable": False, "provider": "clerk"},
        )

    requires_active_org = request.url.path != f"{settings.api_prefix}/auth/me"
    if requires_active_org and not active_organization_header:
        counter(
            "eduground_auth_failures_total",
            labels={"reason": "missing_active_org"},
            description="Authentication failures by reason",
        )
        logger.warning("Missing active organization header")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "missing_active_organization",
                "message": f"{settings.clerk_active_organization_header} header is required for organization-scoped requests",
                "retryable": False,
            },
        )

    try:
        session = get_clerk_token_verifier().verify(token)
        auth_service = AuthService(db)
        user, membership = auth_service.sync_clerk_session(
            session,
            active_organization_id=active_organization_header,
        )
    except ClerkAuthenticationError as exc:
        counter(
            "eduground_auth_failures_total",
            labels={"reason": "invalid_token"},
            description="Authentication failures by reason",
        )
        logger.warning("Clerk token verification failed", extra={"extra_json": {"error": str(exc)}})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_clerk_token", "message": str(exc), "retryable": False, "provider": "clerk"},
        ) from exc
    except ClerkManagementError as exc:
        counter(
            "eduground_auth_failures_total",
            labels={"reason": "sync_failed"},
            description="Authentication failures by reason",
        )
        logger.error("Clerk identity synchronization failed", extra={"extra_json": {"error": str(exc)}})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "clerk_sync_failed",
                "message": f"Unable to synchronize Clerk identity data: {exc}",
                "retryable": True,
                "provider": "clerk",
            },
        ) from exc

    if requires_active_org and membership is None:
        counter(
            "eduground_auth_failures_total",
            labels={"reason": "missing_membership"},
            description="Authentication failures by reason",
        )
        logger.warning(
            "Authenticated request did not resolve an organization membership",
            extra={"extra_json": {"active_organization_id": active_organization_header or session.active_organization_id}},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "organization_membership_required",
                "message": "The active Clerk organization is not linked to a valid membership in Eduground",
                "retryable": False,
                "provider": "clerk",
            },
        )

    return RequestAuthContext(
        session=session,
        user=user,
        membership=membership,
        active_organization_id=active_organization_header or session.active_organization_id,
        auth_user=auth_service.build_auth_user(
            user=user,
            active_membership=membership,
            active_organization_id=active_organization_header or session.active_organization_id,
        ),
    )


def get_current_user(context: Annotated[RequestAuthContext, Depends(get_auth_context)]) -> User:
    return context.user


def get_current_membership(context: Annotated[RequestAuthContext, Depends(get_auth_context)]) -> InstitutionMembership | None:
    return context.membership
