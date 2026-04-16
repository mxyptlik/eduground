from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient
from jwt.exceptions import ExpiredSignatureError, InvalidIssuerError, PyJWTError

from app.core.config import settings
from app.core.errors import DependencyUnavailableError, ProviderRequestError
from app.core.observability import traced_operation


class ClerkConfigurationError(DependencyUnavailableError):
    def __init__(self, message: str) -> None:
        super().__init__(code="clerk_configuration_error", message=message, provider="clerk", retryable=False)


class ClerkAuthenticationError(ProviderRequestError):
    def __init__(self, message: str) -> None:
        super().__init__(code="clerk_authentication_error", message=message, status_code=401, retryable=False, provider="clerk")


class ClerkManagementError(ProviderRequestError):
    def __init__(self, message: str, *, status_code: int = 502, retryable: bool = True) -> None:
        super().__init__(code="clerk_management_error", message=message, status_code=status_code, retryable=retryable, provider="clerk")


@dataclass(slots=True)
class ClerkSession:
    claims: dict[str, Any]
    subject_id: str
    active_organization_id: str | None
    organization_role: str | None
    organization_slug: str | None


class ClerkTokenVerifier:
    def __init__(self) -> None:
        if not settings.clerk_jwks_url:
            raise ClerkConfigurationError("CURRICULUM_TUTOR_CLERK_JWKS_URL is required when Clerk auth is enabled")
        self._jwks_client = PyJWKClient(settings.clerk_jwks_url)

    def verify(self, token: str) -> ClerkSession:
        try:
            with traced_operation(
                "provider.clerk.verify_token",
                metric_name="eduground_provider_call",
                metric_labels={"provider": "clerk", "operation": "verify_token"},
            ):
                signing_key = self._jwks_client.get_signing_key_from_jwt(token)
                claims = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["RS256"],
                    issuer=settings.clerk_jwt_issuer if settings.clerk_jwt_issuer else None,
                    leeway=settings.clerk_jwt_leeway_seconds,
                    options={"verify_aud": False},
                )
        except ExpiredSignatureError as exc:  # pragma: no cover - exercised via runtime auth
            raise ClerkAuthenticationError("Clerk session token expired. Sign in again to refresh the session.") from exc
        except InvalidIssuerError as exc:  # pragma: no cover - exercised via runtime auth
            raise ClerkAuthenticationError(
                "Clerk session token issuer did not match CURRICULUM_TUTOR_CLERK_JWT_ISSUER."
            ) from exc
        except PyJWTError as exc:  # pragma: no cover - exercised via runtime auth
            detail = str(exc).strip() or exc.__class__.__name__
            raise ClerkAuthenticationError(f"Clerk session token verification failed: {detail}") from exc
        except Exception as exc:  # pragma: no cover - exercised via runtime auth
            detail = str(exc).strip() or exc.__class__.__name__
            raise ClerkAuthenticationError(f"Clerk session token verification failed: {detail}") from exc

        authorized_parties = settings.clerk_authorized_party_list
        if authorized_parties:
            azp = claims.get("azp")
            if azp not in authorized_parties:
                raise ClerkAuthenticationError("Clerk session token was issued for a different frontend")

        subject_id = claims.get("sub")
        if not isinstance(subject_id, str) or not subject_id.strip():
            raise ClerkAuthenticationError("Clerk session token did not include a subject")

        active_org_id = claims.get("org_id")
        org_role = claims.get("org_role")
        org_slug = claims.get("org_slug")

        organization_claim = claims.get("o")
        if isinstance(organization_claim, dict):
            active_org_id = active_org_id if isinstance(active_org_id, str) else organization_claim.get("id")
            org_role = org_role if isinstance(org_role, str) else organization_claim.get("rol")
            org_slug = org_slug if isinstance(org_slug, str) else organization_claim.get("slg")

        if active_org_id is not None and not isinstance(active_org_id, str):
            active_org_id = None
        if org_role is not None and not isinstance(org_role, str):
            org_role = None
        if org_slug is not None and not isinstance(org_slug, str):
            org_slug = None

        return ClerkSession(
            claims=claims,
            subject_id=subject_id,
            active_organization_id=active_org_id,
            organization_role=org_role,
            organization_slug=org_slug,
        )


class ClerkManagementClient:
    def __init__(self) -> None:
        if not settings.clerk_secret_key:
            raise ClerkConfigurationError("CURRICULUM_TUTOR_CLERK_SECRET_KEY is required when Clerk auth is enabled")
        self._client = httpx.Client(
            base_url=settings.clerk_api_url.rstrip("/"),
            timeout=httpx.Timeout(settings.clerk_http_timeout_seconds),
            headers={
                "Authorization": f"Bearer {settings.clerk_secret_key}",
                "Accept": "application/json",
            },
        )

    def get_user(self, user_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/users/{user_id}")

    def get_organization(self, organization_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/organizations/{organization_id}")

    def _request_json(self, method: str, path: str) -> dict[str, Any]:
        try:
            with traced_operation(
                "provider.clerk.management_request",
                metric_name="eduground_provider_call",
                metric_labels={"provider": "clerk", "operation": path.strip('/').replace('/', '_') or method.lower()},
            ):
                response = self._client.request(method, path)
        except httpx.TimeoutException as exc:
            raise ClerkManagementError(
                f"Clerk API request timed out while fetching {path}",
                status_code=504,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ClerkManagementError(
                f"Clerk API request could not be completed while fetching {path}",
                status_code=502,
                retryable=True,
            ) from exc
        if response.status_code >= 400:
            raise ClerkManagementError(
                f"Clerk API request failed: {response.status_code} {response.text}",
                status_code=502 if response.status_code >= 500 else 424,
                retryable=response.status_code >= 500 or response.status_code == 429,
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ClerkManagementError("Clerk API response was not an object")
        return payload


@lru_cache(maxsize=1)
def get_clerk_management_client() -> ClerkManagementClient:
    return ClerkManagementClient()
