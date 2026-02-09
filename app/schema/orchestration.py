"""
Orchestration models — fix planning, execution, and convergence.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FixAction(BaseModel):
    """A single fix action to execute."""

    tool_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    target_issues: list[str] = Field(default_factory=list)
    reasoning: str = ""
    is_fallback: bool = False  # True when this is a PDF fallback for an editable doc


class FixPlan(BaseModel):
    """Ordered list of fixes to apply in one iteration."""

    job_id: str
    iteration: int
    actions: list[FixAction] = Field(default_factory=list)
    skipped_issues: list[str] = Field(default_factory=list)


class ConvergenceState(BaseModel):
    """Snapshot of issue counts at one iteration boundary."""

    iteration: int
    issues_before: int
    issues_after: int
    critical_before: int
    critical_after: int
    warning_before: int = 0
    warning_after: int = 0
    fixes_applied: int
    fixes_failed: int
    used_fallback: bool = False  # True if PDF fallback tools were used this iteration


class OrchestrationResult(BaseModel):
    """Summary returned by the fix loop."""

    job_id: str
    iterations: int
    total_fixes_applied: int = 0
    total_fixes_failed: int = 0
    initial_issues: int = 0
    final_issues: int = 0
    initial_critical: int = 0
    final_critical: int = 0
    converged: bool = False
    used_fallback: bool = False  # True if any PDF fallback tools were used
    stop_reason: str = ""


class OrchestrationResponse(BaseModel):
    """API response for orchestration status."""

    job_id: str
    status: str
    result: OrchestrationResult | None = None
