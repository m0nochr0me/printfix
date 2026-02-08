"""
Diagnosis request/response models.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class IssueSeverity(StrEnum):
    critical = "critical"
    warning = "warning"
    info = "info"


class IssueType(StrEnum):
    clipped_content = "clipped_content"
    margin_violation = "margin_violation"
    orphan_widow = "orphan_widow"
    misaligned_elements = "misaligned_elements"
    image_overflow = "image_overflow"
    text_overflow = "text_overflow"
    small_font = "small_font"
    wrong_orientation = "wrong_orientation"
    blank_page = "blank_page"
    visual_inconsistency = "visual_inconsistency"
    non_embedded_font = "non_embedded_font"
    rgb_colorspace = "rgb_colorspace"
    low_dpi_image = "low_dpi_image"
    page_size_mismatch = "page_size_mismatch"
    inconsistent_margins = "inconsistent_margins"
    bad_page_break = "bad_page_break"
    table_overflow = "table_overflow"
    hidden_content = "hidden_content"
    tracked_changes = "tracked_changes"


class IssueSource(StrEnum):
    visual = "visual"
    structural = "structural"
    merged = "merged"


class DiagnosisIssue(BaseModel):
    """A single detected issue."""

    type: IssueType
    severity: IssueSeverity
    source: IssueSource
    page: int | None = None
    location: str | None = None
    description: str
    suggested_fix: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)


class PageDiagnosis(BaseModel):
    """Diagnosis results for a single page."""

    page: int
    issues: list[DiagnosisIssue] = Field(default_factory=list)


class DiagnosisSummary(BaseModel):
    """Aggregate summary of diagnosis results."""

    total_issues: int = 0
    critical_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    top_issues: list[str] = Field(default_factory=list)
    print_readiness: str = "unknown"


class DocumentDiagnosis(BaseModel):
    """Complete diagnosis for a document."""

    job_id: str
    effort_level: str
    file_type: str
    page_count: int
    pages: list[PageDiagnosis] = Field(default_factory=list)
    document_issues: list[DiagnosisIssue] = Field(default_factory=list)
    summary: DiagnosisSummary


class DiagnosisResponse(BaseModel):
    """API response for GET /jobs/{job_id}/diagnosis."""

    job_id: str
    status: str
    diagnosis: DocumentDiagnosis | None = None
    cached: bool = False
