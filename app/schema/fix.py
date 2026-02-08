"""
Fix result and log models.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class FixResult(BaseModel):
    """Result of applying a single fix tool."""

    tool_name: str
    job_id: str
    success: bool
    description: str
    pages_affected: list[int] = Field(default_factory=list)
    before_value: str | None = None
    after_value: str | None = None
    error: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FixLog(BaseModel):
    """Full log of fixes applied to a job."""

    job_id: str
    fixes: list[FixResult] = Field(default_factory=list)
    total_applied: int = 0
    total_failed: int = 0
