"""
REST API router
"""


from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import verify_token

__all__ = ("router",)

router = APIRouter(
    prefix="/v1",
    tags=["memory"],
    dependencies=[Depends(verify_token)],
)


# TODO