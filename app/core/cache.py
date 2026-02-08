"""
Caching
"""

# pyright: basic

from hashlib import blake2s

from aiocache import RedisCache
from aiocache.serializers import JsonSerializer

from app.core.config import settings

__all__ = ("cache", "make_cache_key")

cache = RedisCache(
    serializer=JsonSerializer(),
    namespace=settings.PROJECT_NAME,
    endpoint=settings.REDIS_HOST,
    password=settings.REDIS_PASSWORD,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB_CACHE,
)


def make_cache_key(*args: str) -> str:
    """Create a cache key by hashing the given arguments."""
    h = blake2s()
    for arg in args:
        h.update(arg.encode("utf-8"))
    return h.hexdigest()
