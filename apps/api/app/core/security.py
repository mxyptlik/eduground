from __future__ import annotations

import base64
import hashlib
import hmac
import time
from functools import lru_cache
from typing import Mapping

from app.core.config import settings
from app.integrations.auth.clerk import ClerkTokenVerifier


class WebhookVerificationError(RuntimeError):
    pass


def _decode_svix_secret(secret: str) -> bytes:
    normalized = secret.removeprefix("whsec_")
    return base64.b64decode(normalized)


def verify_svix_webhook(*, payload: bytes, headers: Mapping[str, str]) -> None:
    if not settings.clerk_webhook_signing_secret:
        raise WebhookVerificationError("CURRICULUM_TUTOR_CLERK_WEBHOOK_SIGNING_SECRET is required for Clerk webhooks")

    msg_id = headers.get("svix-id")
    timestamp = headers.get("svix-timestamp")
    signature_header = headers.get("svix-signature")
    if not msg_id or not timestamp or not signature_header:
        raise WebhookVerificationError("Missing Svix webhook headers")

    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise WebhookVerificationError("Invalid Svix timestamp") from exc

    if abs(time.time() - timestamp_value) > 300:
        raise WebhookVerificationError("Stale Clerk webhook timestamp")

    signed_payload = b".".join([msg_id.encode("utf-8"), timestamp.encode("utf-8"), payload])
    signing_key = _decode_svix_secret(settings.clerk_webhook_signing_secret)
    expected = base64.b64encode(hmac.new(signing_key, signed_payload, hashlib.sha256).digest()).decode("utf-8")

    provided_signatures = [
        part.split(",", maxsplit=1)[1]
        for part in signature_header.split(" ")
        if part.startswith("v1,") and "," in part
    ]
    if not any(hmac.compare_digest(signature, expected) for signature in provided_signatures):
        raise WebhookVerificationError("Invalid Clerk webhook signature")


@lru_cache(maxsize=1)
def get_clerk_token_verifier() -> ClerkTokenVerifier:
    return ClerkTokenVerifier()
