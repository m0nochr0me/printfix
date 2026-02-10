"""
Redis-hash-backed job state manager.

Each job is stored as a Redis hash at key ``printfix:job:{job_id}`` with fields
for status, timestamps, metadata, and processing results.
"""

import json
from datetime import UTC, datetime

from redis.asyncio import Redis

from app.core.config import settings
from app.core.log import logger

__all__ = ("JobStateManager",)

VALID_TRANSITIONS: dict[str, set[str]] = {
    "uploaded": {"ingesting", "failed"},
    "ingesting": {"converting", "failed"},
    "converting": {"rendering", "failed"},
    "rendering": {"ingested", "failed"},
    "ingested": {"diagnosing", "failed"},
    "diagnosing": {"diagnosed", "failed"},
    "diagnosed": {"diagnosing", "fixing", "failed"},
    "fixing": {"fixing", "verifying", "failed"},
    "verifying": {"done", "needs_review", "failed"},
    "needs_review": {"done", "needs_review", "fixing", "failed"},
    "done": {"needs_review"},
}


class JobStateManager:
    """Manages job lifecycle state in Redis."""

    _redis: Redis | None = None

    @classmethod
    async def get_redis(cls) -> Redis:
        if cls._redis is None:
            cls._redis = Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB_JOBS,
                password=settings.REDIS_PASSWORD,
                decode_responses=True,
            )
        return cls._redis

    @classmethod
    async def close(cls) -> None:
        if cls._redis is not None:
            await cls._redis.aclose()
            cls._redis = None

    @staticmethod
    def _key(job_id: str) -> str:
        return f"printfix:job:{job_id}"

    @classmethod
    async def create_job(cls, job_id: str, *, original_filename: str, **kwargs: str) -> None:
        r = await cls.get_redis()
        now = datetime.now(UTC).isoformat()
        mapping: dict[str, str] = {
            "id": job_id,
            "status": "uploaded",
            "original_filename": original_filename,
            "created_at": now,
            "updated_at": now,
        }
        for k, v in kwargs.items():
            mapping[k] = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
        await r.hset(cls._key(job_id), mapping=mapping) # type: ignore
        await r.expire(cls._key(job_id), settings.JOB_TTL_SECONDS)

    @classmethod
    async def set_state(
        cls,
        job_id: str,
        new_status: str,
        *,
        error: str | None = None,
        extra: dict | None = None,
    ) -> None:
        r = await cls.get_redis()
        current = await r.hget(cls._key(job_id), "status") # type: ignore
        if current and new_status not in VALID_TRANSITIONS.get(current, set()):
            logger.warning(
                f"Job {job_id}: invalid transition {current} → {new_status}"
            )

        now = datetime.now(UTC).isoformat()
        updates: dict[str, str] = {
            "status": new_status,
            "updated_at": now,
        }
        if error:
            updates["error"] = error
        if new_status in ("done", "failed", "needs_review"):
            updates["completed_at"] = now
        if extra:
            for k, v in extra.items():
                updates[k] = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
        await r.hset(cls._key(job_id), mapping=updates) # type: ignore

    @classmethod
    async def get_job(cls, job_id: str) -> dict | None:
        r = await cls.get_redis()
        data = await r.hgetall(cls._key(job_id)) # type: ignore
        return dict(data) if data else None

    @classmethod
    async def delete_job(cls, job_id: str) -> bool:
        r = await cls.get_redis()
        return bool(await r.delete(cls._key(job_id)))

    @classmethod
    async def list_jobs(cls, limit: int = 100) -> list[dict]:
        """List recent jobs, sorted by creation time descending."""
        r = await cls.get_redis()
        jobs: list[dict] = []
        async for key in r.scan_iter(match="printfix:job:*", count=200):
            data = await r.hgetall(key)
            if data:
                jobs.append(dict(data))
        jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
        return jobs[:limit]
