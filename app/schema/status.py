"""
Health check response model
"""

from pydantic import BaseModel, Field
from ulid import ULID


class HealthCheckResponse(BaseModel):
    """
    Health check response model.
    """

    status: str = Field(default="OK", description="Service status")
    version: str = Field(..., description="Service version")
    uptime: float = Field(..., description="Service uptime")
    exec_id: ULID = Field(..., description="Service execution ID")


class IndexResponse(BaseModel):
    """
    Index response model.
    """

    message: str = Field(default="OK", description="Welcome message")
