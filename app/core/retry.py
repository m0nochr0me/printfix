"""Async retry helper with exponential backoff."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, TypeVar

from app.core.log import logger

__all__ = ("with_retry",)

T = TypeVar("T")


async def with_retry(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable: tuple[type[BaseException], ...] = (Exception,),
    label: str = "",
    **kwargs: Any,
) -> T:
    """
    Call *fn* with exponential backoff retries.

    Parameters
    ----------
    fn : async callable to invoke
    max_retries : number of retry attempts (0 = no retries, just call once)
    base_delay : initial delay in seconds (doubles each attempt)
    max_delay : ceiling for the delay
    retryable : exception types that trigger a retry
    label : human-readable name for log messages
    """
    last_exc: BaseException | None = None
    tag = label or getattr(fn, "__name__", "unknown")

    for attempt in range(max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except retryable as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            delay = min(base_delay * (2 ** attempt), max_delay)
            logger.warning(
                f"{tag}: attempt {attempt + 1}/{max_retries + 1} failed "
                f"({type(exc).__name__}: {exc}), retrying in {delay:.1f}s"
            )
            await asyncio.sleep(delay)

    raise last_exc  # type: ignore[misc]
