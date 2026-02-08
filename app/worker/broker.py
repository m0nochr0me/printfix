"""
Taskiq broker configuration.

Imported by both the FastAPI process (to enqueue tasks) and the taskiq worker CLI
(to consume and execute tasks). Keep this module free of app.main imports to avoid
circular dependencies.
"""

import taskiq_fastapi
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend

from app.core.config import settings

__all__ = ("broker",)


def _redis_url(db: int) -> str:
    auth = f":{settings.REDIS_PASSWORD}@" if settings.REDIS_PASSWORD else ""
    return f"redis://{auth}{settings.REDIS_HOST}:{settings.REDIS_PORT}/{db}"


result_backend = RedisAsyncResultBackend(
    redis_url=_redis_url(settings.REDIS_DB_RESULTS),
    result_ex_time=3600,
)

broker = ListQueueBroker(
    url=_redis_url(settings.REDIS_DB_BROKER),
    queue_name="printfix:tasks",
).with_result_backend(result_backend)

# Wire broker to FastAPI app for dependency resolution in tasks.
# Uses a string path to avoid circular imports — resolved lazily.
taskiq_fastapi.init(broker, "app.main:app")
