"""
Verification and confidence scoring models.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class PageComparison(BaseModel):
    """Before/after comparison for a single page."""

    page: int
    before_image: str  # path to pre-fix page image
    after_image: str  # path to post-fix page image
    issues_before: int = 0
    issues_after: int = 0
    confidence: float = Field(ge=0.0, le=100.0, default=0.0)
    notes: str = ""


class ConfidenceBreakdown(BaseModel):
    """Detailed breakdown of confidence scoring factors."""

    base_score: float = Field(ge=0.0, le=100.0, description="Base score from issue resolution")
    convergence_bonus: float = Field(
        ge=-20.0, le=20.0, default=0.0,
        description="Bonus/penalty from convergence quality",
    )
    severity_penalty: float = Field(
        ge=-50.0, le=0.0, default=0.0,
        description="Penalty for remaining critical/warning issues",
    )
    visual_score: float = Field(
        ge=0.0, le=100.0, default=100.0,
        description="AI visual quality assessment (0-100)",
    )
    visual_weight: float = Field(
        ge=0.0, le=1.0, default=0.3,
        description="Weight given to visual score",
    )
    final_score: float = Field(ge=0.0, le=100.0)

    @property
    def print_readiness(self) -> str:
        if self.final_score >= 90:
            return "print_ready"
        if self.final_score >= 70:
            return "likely_fine"
        if self.final_score >= 50:
            return "needs_review"
        return "manual_intervention"


class FixReportEntry(BaseModel):
    """A single entry in the human-readable fix report."""

    issue_type: str
    severity: str
    description: str
    action_taken: str  # "fixed", "skipped", "failed", "not_applicable"
    tool_used: str | None = None
    details: str = ""


class FixReport(BaseModel):
    """Human-readable report of what was changed and why."""

    job_id: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    original_filename: str
    effort_level: str
    aggressiveness: str
    total_pages: int = 0
    issues_found: int = 0
    issues_fixed: int = 0
    issues_skipped: int = 0
    issues_remaining: int = 0
    iterations: int = 0
    confidence: float = 0.0
    print_readiness: str = "unknown"
    entries: list[FixReportEntry] = Field(default_factory=list)
    summary: str = ""


class VerificationResult(BaseModel):
    """Complete verification output — persisted to disk."""

    job_id: str
    confidence: ConfidenceBreakdown
    page_comparisons: list[PageComparison] = Field(default_factory=list)
    report: FixReport
    approved: bool = False
    auto_approved: bool = False
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class VerificationResponse(BaseModel):
    """API response for verification/report endpoints."""

    job_id: str
    status: str
    verification: VerificationResult | None = None
