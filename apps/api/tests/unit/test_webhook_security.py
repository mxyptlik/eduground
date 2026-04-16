from __future__ import annotations

import pytest

from app.core.security import WebhookVerificationError, verify_svix_webhook

pytestmark = pytest.mark.unit


def test_verify_svix_webhook_accepts_valid_signature(set_setting, svix_secret, sign_svix_payload) -> None:
    set_setting("clerk_webhook_signing_secret", svix_secret)
    payload, headers = sign_svix_payload(
        svix_secret,
        {"type": "user.created", "data": {"id": "user_123"}},
    )

    verify_svix_webhook(payload=payload, headers=headers)


def test_verify_svix_webhook_rejects_missing_headers(set_setting, svix_secret) -> None:
    set_setting("clerk_webhook_signing_secret", svix_secret)

    with pytest.raises(WebhookVerificationError, match="Missing Svix webhook headers"):
        verify_svix_webhook(payload=b"{}", headers={})


def test_verify_svix_webhook_rejects_invalid_signature(set_setting, svix_secret, sign_svix_payload) -> None:
    set_setting("clerk_webhook_signing_secret", svix_secret)
    payload, headers = sign_svix_payload(
        svix_secret,
        {"type": "user.created", "data": {"id": "user_123"}},
    )
    headers["svix-signature"] = "v1,invalid-signature"

    with pytest.raises(WebhookVerificationError, match="Invalid Clerk webhook signature"):
        verify_svix_webhook(payload=payload, headers=headers)
