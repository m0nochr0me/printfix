"""
Shared dependencies for the REST API.
"""

import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

_bearer_scheme = HTTPBearer()


async def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),  # noqa: B008
) -> None:
    """Validate Bearer token against APP_AUTH_KEY."""
    if not secrets.compare_digest(credentials.credentials, settings.APP_AUTH_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )


async def check_rate_limit(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),  # noqa: B008
) -> None:
    """Check sliding-window rate limit for the authenticated client."""
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        return  # Rate limiting not configured (e.g. tests)

    # Use first 8 chars of token as identity key
    key = credentials.credentials[:8]
    allowed, _remaining = await limiter.check(key)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(settings.RATE_LIMIT_WINDOW_SECONDS)},
        )
