"""Helpers for building task API JWTs in tests."""

from datetime import UTC, datetime, timedelta

import jwt


def make_task_api_jwt(
    *,
    secret: str,
    audience: str,
    sub: str,
    org_id: str,
    expires_in: timedelta = timedelta(minutes=5),
    issued_at: datetime | None = None,
) -> str:
    """Create a signed JWT carrying required task API claims."""
    now = issued_at or datetime.now(UTC)
    payload = {
        "sub": sub,
        "org_id": org_id,
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_in).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")
