"""HTTP middleware for endpoint-specific rate limiting."""

from __future__ import annotations

import asyncio
import math
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

    def __post_init__(self) -> None:
        """Normalize the rule method once so matching remains deterministic."""
        normalized_method = self.method.strip().upper()
        if not normalized_method:
            raise ValueError("RateLimitRule.method must not be blank")
        object.__setattr__(self, "method", normalized_method)


class _SlidingWindowLimiter:
    """Thread-safe in-memory sliding-window limiter keyed by endpoint identity."""

    def __init__(self) -> None:
        self._requests: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    async def allow(
        self,
        *,
        key: str,
        max_requests: int,
        window_seconds: float,
    ) -> tuple[bool, int | None]:
        """Return allow/deny and optional retry-after seconds."""
        now = time.monotonic()
        cutoff = now - window_seconds

        async with self._lock:
            self._cleanup_expired(cutoff=cutoff)
            history = self._requests.setdefault(key, [])
            if len(history) >= max_requests:
                retry_after_seconds = window_seconds - (now - history[0])
                retry_after = max(1, math.ceil(retry_after_seconds))
                return False, retry_after
            history.append(now)

        return True, None

    def _cleanup_expired(self, *, cutoff: float) -> None:
        """Evict expired timestamps and drop keys with empty windows."""
        for request_key in list(self._requests.keys()):
            history = self._requests[request_key]
            history[:] = [value for value in history if value > cutoff]
            if not history:
                del self._requests[request_key]


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
        allowed, retry_after = await self._limiter.allow(
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
        if request.client is None or not request.client.host:
            return UNKNOWN_CLIENT_KEY
        client_host = request.client.host
        if rule.identity_header is not None:
            identity_from_header = request.headers.get(rule.identity_header)
            if identity_from_header and identity_from_header.strip():
                return f"{client_host}:{identity_from_header.strip()}"
        return client_host
