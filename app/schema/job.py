"""
Job request/response models.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    uploaded = "uploaded"
    validating = "validating"
    ingesting = "ingesting"
    converting = "converting"
    rendering = "rendering"
    ingested = "ingested"
    diagnosing = "diagnosing"
    diagnosed = "diagnosed"
    fixing = "fixing"
    verifying = "verifying"
    done = "done"
    needs_review = "needs_review"
    failed = "failed"


class EffortLevel(StrEnum):
    quick = "quick"
    standard = "standard"
    thorough = "thorough"


class Aggressiveness(StrEnum):
    conservative = "conservative"
    moderate = "moderate"
    aggressive = "aggressive"
    smart_auto = "smart_auto"


class PageSize(StrEnum):
    a4 = "a4"
    letter = "letter"
    original = "original"


class ColorSpace(StrEnum):
    cmyk = "cmyk"
    rgb = "rgb"
    original = "original"


class JobCreateResponse(BaseModel):
    id: str
    status: JobStatus = JobStatus.uploaded
    original_filename: str
    created_at: datetime


class JobResponse(BaseModel):
    id: str
    status: JobStatus
    effort: EffortLevel
    aggressiveness: Aggressiveness
    original_filename: str
    file_type: str | None = None
    file_size_bytes: int | None = None
    pages: int | None = None
    issues_found: int = 0
    issues_fixed: int = 0
    issues_skipped: int = 0
    confidence: float | None = None
    print_readiness: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


class JobPreviewResponse(BaseModel):
    job_id: str
    pages: list[str] = Field(default_factory=list, description="List of page image paths")
    page_count: int = 0
