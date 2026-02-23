"""HTTP middleware for endpoint-specific rate limiting."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Final

from fastapi import Request, status
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from agenticai.api.responses import build_error_response

UNKNOWN_CLIENT_KEY: Final[str] = "unknown-client"


@dataclass(frozen=True)
class RateLimitRule:
    """One endpoint-specific rate-limit policy."""

    method: str
    path: str
    max_requests: int
    window_seconds: float
    error_code: str
    error_message: str
    identity_header: str | None = None


class _SlidingWindowLimiter:
    """Thread-safe in-memory sliding-window limiter keyed by endpoint identity."""

    def __init__(self) -> None:
        self._requests: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(
        self,
        *,
        key: str,
        max_requests: int,
        window_seconds: float,
    ) -> tuple[bool, int | None]:
        """Return allow/deny and optional retry-after seconds."""
        now = time.monotonic()
        cutoff = now - window_seconds

        with self._lock:
            history = self._requests.setdefault(key, [])
            history[:] = [value for value in history if value > cutoff]
            if len(history) >= max_requests:
                retry_after_seconds = window_seconds - (now - history[0])
                retry_after = max(1, math.ceil(retry_after_seconds))
                return False, retry_after
            history.append(now)

        return True, None


class EndpointRateLimitMiddleware(BaseHTTPMiddleware):
    """Apply route-specific rate limits before request handlers execute."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        enabled: bool,
        rules: tuple[RateLimitRule, ...],
    ) -> None:
        super().__init__(app)
        self._enabled = enabled
        self._rules = rules
        self._limiter = _SlidingWindowLimiter()

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._enabled:
            return await call_next(request)

        matching_rule = self._match_rule(method=request.method, path=request.url.path)
        if matching_rule is None:
            return await call_next(request)

        identity = self._identity_for_request(request=request, rule=matching_rule)
        key = f"{matching_rule.method}:{matching_rule.path}:{identity}"
        allowed, retry_after = self._limiter.allow(
            key=key,
            max_requests=matching_rule.max_requests,
            window_seconds=matching_rule.window_seconds,
        )
        if not allowed:
            response = build_error_response(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                code=matching_rule.error_code,
                message=matching_rule.error_message,
            )
            if retry_after is not None:
                response.headers["Retry-After"] = str(retry_after)
            return response

        return await call_next(request)

    def _match_rule(self, *, method: str, path: str) -> RateLimitRule | None:
        request_method = method.upper()
        for rule in self._rules:
            if rule.method == request_method and rule.path == path:
                return rule
        return None

    def _identity_for_request(self, *, request: Request, rule: RateLimitRule) -> str:
        if rule.identity_header is not None:
            identity_from_header = request.headers.get(rule.identity_header)
            if identity_from_header and identity_from_header.strip():
                return identity_from_header.strip()
        if request.client is None or not request.client.host:
            return UNKNOWN_CLIENT_KEY
        return request.client.host
