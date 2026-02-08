"""
Database connection management for LanceDB asynchronous operations.
"""

from asyncio import AbstractEventLoop, get_running_loop
from dataclasses import dataclass

import lancedb

from app.core.config import settings

__all__ = (
    "close_db",
    "get_db",
)


@dataclass
class DBConnection:
    loop: AbstractEventLoop | None = None
    db: lancedb.AsyncConnection | None = None


_STATE = DBConnection()


async def get_db() -> lancedb.AsyncConnection:

    loop = get_running_loop()
    state = _STATE

    if state.db is None or not state.db.is_open() or state.loop is not loop:
        if state.db is not None and state.db.is_open():
            state.db.close()
        state.db = await lancedb.connect_async(settings.VECTOR_STORE_PATH)
        state.loop = loop
    return state.db


async def close_db() -> None:
    state = _STATE
    if state.db is not None and state.db.is_open():
        state.db.close()
        state.db = None
        state.loop = None
