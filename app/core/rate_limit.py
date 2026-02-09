"""Redis-backed sliding window rate limiter."""

from __future__ import annotations

import time

from redis.asyncio import Redis

from app.core.config import settings

__all__ = ("RateLimiter",)


class RateLimiter:
    """Sliding window counter rate limiter using Redis sorted sets."""

    def __init__(self, redis: Redis, prefix: str = "printfix:ratelimit"):
        self.redis = redis
        self.prefix = prefix

    async def check(
        self,
        key: str,
        limit: int | None = None,
        window_seconds: int | None = None,
    ) -> tuple[bool, int]:
        """
        Check if a request is allowed under the rate limit.

        Returns (allowed, remaining_requests).
        """
        limit = limit or settings.RATE_LIMIT_REQUESTS
        window_seconds = window_seconds or settings.RATE_LIMIT_WINDOW_SECONDS

        now = time.time()
        window_key = f"{self.prefix}:{key}"

        pipe = self.redis.pipeline()
        pipe.zremrangebyscore(window_key, 0, now - window_seconds)
        pipe.zadd(window_key, {str(now): now})
        pipe.zcard(window_key)
        pipe.expire(window_key, window_seconds)
        results = await pipe.execute()

        count = results[2]
        allowed = count <= limit
        remaining = max(0, limit - count)

        if not allowed:
            # Roll back the just-added entry
            await self.redis.zrem(window_key, str(now))

        return allowed, remaining

    async def close(self) -> None:
        await self.redis.aclose()
