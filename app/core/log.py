"""
Logging
"""

# pyright: basic

import logging

from asgi_correlation_id.context import correlation_id
from loguru import logger

from app.core.config import settings
from app.schema.log_entry import LogEntry

__all__ = (
    "log_serializer",
    "logger",
    "sink",
    "uvicorn_log_config",
)


class InterceptHandler(logging.Handler):
    def emit(self, record):
        # Get corresponding Loguru level
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller to get correct stack depth
        frame, depth = logging.currentframe(), 2
        while frame.f_back and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


uvicorn_log_config = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "correlation_id": {
            "()": "asgi_correlation_id.CorrelationIdFilter",
            "default_value": "",
        },
    },
    "formatters": {
        "default": {
            "format": '{"asctime":"%(asctime)s","levelname":"%(levelname)s","message":"%(correlation_id)s - %(name)s - %(message)s"}',
        },
    },
    "handlers": {
        "default": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "stream": "ext://sys.stdout",
            "filters": ["correlation_id"],
        },
    },
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.access": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"handlers": ["default"], "level": "ERROR", "propagate": False},
    },
    "root": {"handlers": ["default"], "level": "DEBUG" if settings.DEBUG else "INFO"},
}


def log_serializer(record) -> str:
    """
    Custom log serializer for loguru
    """

    cid = correlation_id.get() or ""
    message = record["message"]
    if len(message) > settings.LOG_MESSAGE_MAX_LEN:
        message = message[: settings.LOG_MESSAGE_MAX_LEN - 3] + "..."

    log_entry = LogEntry(
        asctime=record["time"],
        levelname=record["level"].name,
        message=f"{cid} - {record['name']} - {message}",
    )

    return log_entry.model_dump_json()


# async def asink(message) -> None:
#     """
#     Custom sink for loguru
#     """
#     await aiofiles.stdout.write(log_serializer(message.record) + "\n")
#     await aiofiles.stdout.flush()


def sink(message) -> None:
    """
    Custom sink for loguru
    """
    print(log_serializer(message.record))


logger.remove()

logger.add(
    sink,
    level="DEBUG" if settings.DEBUG else "INFO",
)


logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO)
