"""Create a development-only Clerk session token from the terminal.

Uses the existing Clerk secret in Eduground's .env. Never prints the secret or
JWT. Clerk's backend session-creation endpoint is only for development/testing.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))


def request_json(url: str, *, key: str | None = None, body: dict | None = None,
                 session_token: str | None = None) -> dict | list:
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if session_token:
        headers["Authorization"] = f"Bearer {session_token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url, headers=headers,
                  data=json.dumps(body).encode("utf-8") if body is not None else None,
                  method="POST" if body is not None else "GET")
    try:
        with urlopen(req, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from {url.split('?')[0]} (response body suppressed)") from exc
    except URLError as exc:
        raise RuntimeError(f"Cannot reach {url.split('?')[0]}: {type(exc.reason).__name__}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list-users", action="store_true", help="list up to 100 Clerk test users")
    group.add_argument("--user-id", help="existing Clerk development user ID")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token-seconds", type=int, default=600)
    parser.add_argument("--output", type=Path, help="create-only token file; default under evaluation/test_runs")
    args = parser.parse_args()
    if not 60 <= args.token_seconds <= 900:
        parser.error("token-seconds must be 60–900")
    from app.core.config import settings

    key = settings.clerk_secret_key
    if not settings.clerk_enabled or not key:
        parser.error("Clerk is not enabled or its secret key is absent in Eduground .env")
    if not key.startswith("sk_test_"):
        parser.error("this helper only creates sessions with a Clerk development secret key (sk_test_)")
    base = settings.clerk_api_url.rstrip("/")
    if args.list_users:
        users = request_json(base + "/users?" + urlencode({"limit": 100}), key=key)
        if not isinstance(users, list):
            raise RuntimeError("Clerk users response was not a list")
        for user in users:
            emails = [entry.get("email_address") for entry in user.get("email_addresses", [])]
            print(f"{user.get('id')}\t{','.join(email for email in emails if email)}")
        return 0
    session = request_json(base + "/sessions", key=key, body={"user_id": args.user_id})
    if not isinstance(session, dict) or not isinstance(session.get("id"), str):
        raise RuntimeError("Clerk did not return a session ID")
    session_id = session["id"]
    response = request_json(base + f"/sessions/{session_id}/tokens", key=key,
                            body={"expires_in_seconds": args.token_seconds})
    token = response.get("jwt") if isinstance(response, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("Clerk did not return a session JWT")
    target = args.output or ROOT / "evaluation" / "test_runs" / "clerk-tokens" / f"{session_id}.jwt"
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(token + "\n")
    print(f"Token file: {target}")
    print(f"Clerk session: {session_id}; requested token lifetime: {args.token_seconds}s")
    try:
        profile = request_json(args.api_url.rstrip("/") + "/api/auth/me", session_token=token)
    except RuntimeError as exc:
        print(f"Eduground /api/auth/me validation failed: {exc}", file=sys.stderr)
        print("The token file was retained for diagnosis; no token value was printed.", file=sys.stderr)
        return 1
    print(f"Eduground /api/auth/me: OK ({profile.get('id', 'user returned') if isinstance(profile, dict) else 'user returned'})")
    print(f"Created at: {datetime.now(timezone.utc).isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
