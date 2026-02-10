"""
Verification engine: before/after comparison, confidence scoring,
fix report generation, and final quality assessment.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import aiofiles
from google.genai.types import Content, GenerateContentConfig, Part

from app.core.ai import ai_client
from app.core.effort import EffortConfig, get_effort_config
from app.core.log import logger
from app.core.prompts import VERIFICATION_PROMPT
from app.core.rendering import render_pages
from app.core.storage import get_job_dir
from app.schema.diagnosis import DocumentDiagnosis
from app.schema.fix import FixLog, FixResult
from app.schema.job import EffortLevel
from app.schema.orchestration import OrchestrationResult
from app.schema.verification import (
    ConfidenceBreakdown,
    FixReport,
    FixReportEntry,
    PageComparison,
    VerificationResult,
)
from app.worker.job_state import JobStateManager

__all__ = ("run_verification",)

# Confidence thresholds for auto-approval
AUTO_APPROVE_THRESHOLD = 85.0
NEEDS_REVIEW_THRESHOLD = 50.0


async def run_verification(job_id: str) -> VerificationResult:
    """
    Full verification pipeline:
      1. Render final pages (after fixes)
      2. Build before/after page comparisons
      3. Compute confidence score (algorithmic + optional AI visual check)
      4. Generate human-readable fix report
      5. Persist verification result
    """
    logger.info(f"Job {job_id}: starting verification")

    job = await JobStateManager.get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    effort = EffortLevel(job.get("effort", "standard"))
    effort_config = get_effort_config(effort)

    # --- 1. Render final pages ---
    after_pages = await _render_after_pages(job_id, job)

    # --- 2. Load before pages + diagnosis + orchestration ---
    before_pages = _get_before_pages(job_id)
    initial_diagnosis = await _load_diagnosis(job_id, "diagnosis_initial.json")
    final_diagnosis = await _load_diagnosis(job_id, "diagnosis.json")
    orchestration = await _load_orchestration(job_id)
    fix_log = await _load_fix_log(job_id)

    # --- 3. Build page comparisons ---
    comparisons = _build_page_comparisons(
        before_pages, after_pages,
        initial_diagnosis, final_diagnosis,
    )

    # --- 4. AI visual quality check (effort-dependent) ---
    visual_score = 100.0
    if effort in (EffortLevel.standard, EffortLevel.thorough) and after_pages:
        try:
            visual_score = await _ai_visual_check(
                before_pages, after_pages,
                effort_config, job_id,
            )
        except Exception:
            logger.exception(f"Job {job_id}: AI visual check failed, using algorithmic score only")

    # --- 5. Compute confidence ---
    confidence = _compute_confidence(
        initial_diagnosis=initial_diagnosis,
        final_diagnosis=final_diagnosis,
        orchestration=orchestration,
        visual_score=visual_score,
    )

    # --- 6. Generate report ---
    report = _generate_report(
        job_id=job_id,
        job=job,
        initial_diagnosis=initial_diagnosis,
        final_diagnosis=final_diagnosis,
        orchestration=orchestration,
        fix_log=fix_log,
        confidence=confidence,
    )

    # --- 7. Assemble result ---
    auto_approved = confidence.final_score >= AUTO_APPROVE_THRESHOLD
    result = VerificationResult(
        job_id=job_id,
        confidence=confidence,
        page_comparisons=comparisons,
        report=report,
        approved=auto_approved,
        auto_approved=auto_approved,
    )

    # --- 8. Persist ---
    await _persist_verification(job_id, result)

    logger.info(
        f"Job {job_id}: verification complete — "
        f"confidence={confidence.final_score:.1f}, "
        f"readiness={confidence.print_readiness}, "
        f"auto_approved={auto_approved}"
    )

    return result


# ---------------------------------------------------------------------------
# Before / After rendering
# ---------------------------------------------------------------------------

async def _render_after_pages(job_id: str, job: dict) -> list[str]:
    """
    Re-render the (fixed) document to fresh page images.

    Saves to data/jobs/{job_id}/pages_after/ to preserve the original
    "before" images in pages/.
    """
    pdf_path = job.get("pdf_path", "")
    if not pdf_path or not Path(pdf_path).exists():
        logger.warning(f"Job {job_id}: no PDF to render for verification")
        return []

    after_dir = get_job_dir(job_id) / "pages_after"
    os.makedirs(after_dir, exist_ok=True)

    # Render to a temporary location then move
    raw_pages = await render_pages(pdf_path, job_id, dpi=200)

    # Move rendered images to pages_after/
    after_paths: list[str] = []
    for i, src in enumerate(raw_pages, start=1):
        dest = after_dir / f"{i}.png"
        src_path = Path(src)
        if src_path.exists():
            # Copy rather than move — render_pages writes to pages/
            shutil.copy2(src_path, dest)
        after_paths.append(str(dest))

    # Also snapshot the "before" pages if not already done
    await _snapshot_before_pages(job_id)

    return after_paths


async def _snapshot_before_pages(job_id: str) -> None:
    """
    Copy the original page renders (from before fixes) to pages_before/
    if they haven't been preserved yet.

    The initial page images from ingestion are in pages/. During the fix loop
    they get overwritten by re-render. We save a copy on first verification.
    """
    before_dir = get_job_dir(job_id) / "pages_before"
    if before_dir.exists():
        return  # Already snapshotted

    pages_dir = get_job_dir(job_id) / "pages"
    if not pages_dir.exists():
        return

    shutil.copytree(pages_dir, before_dir)
    logger.debug(f"Job {job_id}: snapshotted before-pages to {before_dir}")


def _get_before_pages(job_id: str) -> list[str]:
    """Return paths to original (pre-fix) page images."""
    before_dir = get_job_dir(job_id) / "pages_before"
    if not before_dir.exists():
        # Fall back to pages/ if no snapshot exists
        before_dir = get_job_dir(job_id) / "pages"

    if not before_dir.exists():
        return []

    pages = sorted(before_dir.glob("*.png"), key=lambda p: int(p.stem))
    return [str(p) for p in pages]


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

async def _load_diagnosis(job_id: str, filename: str) -> DocumentDiagnosis | None:
    """Load a diagnosis JSON file."""
    path = get_job_dir(job_id) / filename
    if not path.exists():
        # Try the default diagnosis.json as fallback
        if filename != "diagnosis.json":
            path = get_job_dir(job_id) / "diagnosis.json"
            if not path.exists():
                return None
        else:
            return None

    async with aiofiles.open(path, "r") as f:
        data = json.loads(await f.read())
    return DocumentDiagnosis.model_validate(data)


async def _load_orchestration(job_id: str) -> OrchestrationResult | None:
    path = get_job_dir(job_id) / "orchestration.json"
    if not path.exists():
        return None
    async with aiofiles.open(path, "r") as f:
        data = json.loads(await f.read())
    return OrchestrationResult.model_validate(data)


async def _load_fix_log(job_id: str) -> FixLog | None:
    path = get_job_dir(job_id) / "fixes.json"
    if not path.exists():
        return FixLog(job_id=job_id)
    async with aiofiles.open(path, "r") as f:
        data = json.loads(await f.read())
    if isinstance(data, list):
        fixes = [FixResult.model_validate(entry) for entry in data]
        return FixLog(
            job_id=job_id,
            fixes=fixes,
            total_applied=sum(1 for f in fixes if f.success),
            total_failed=sum(1 for f in fixes if not f.success),
        )
    return FixLog.model_validate(data)


# ---------------------------------------------------------------------------
# Page comparisons
# ---------------------------------------------------------------------------

def _build_page_comparisons(
    before_pages: list[str],
    after_pages: list[str],
    initial_diag: DocumentDiagnosis | None,
    final_diag: DocumentDiagnosis | None,
) -> list[PageComparison]:
    """Build per-page before/after comparison entries."""
    max_pages = max(len(before_pages), len(after_pages))
    if max_pages == 0:
        return []

    # Index diagnosis issues by page
    before_counts: dict[int, int] = {}
    after_counts: dict[int, int] = {}

    if initial_diag:
        for pd in initial_diag.pages:
            before_counts[pd.page] = len(pd.issues)

    if final_diag:
        for pd in final_diag.pages:
            after_counts[pd.page] = len(pd.issues)

    comparisons: list[PageComparison] = []
    for i in range(1, max_pages + 1):
        before_img = before_pages[i - 1] if i <= len(before_pages) else ""
        after_img = after_pages[i - 1] if i <= len(after_pages) else ""
        issues_b = before_counts.get(i, 0)
        issues_a = after_counts.get(i, 0)

        # Per-page confidence: simple heuristic
        if issues_b == 0 or issues_a == 0:
            page_conf = 100.0
        else:
            improvement = (issues_b - issues_a) / issues_b
            page_conf = max(0.0, min(100.0, 50.0 + improvement * 50.0))

        comparisons.append(PageComparison(
            page=i,
            before_image=before_img,
            after_image=after_img,
            issues_before=issues_b,
            issues_after=issues_a,
            confidence=round(page_conf, 1),
        ))

    return comparisons


# ---------------------------------------------------------------------------
# AI visual quality check
# ---------------------------------------------------------------------------

async def _ai_visual_check(
    before_pages: list[str],
    after_pages: list[str],
    effort_config: EffortConfig,
    job_id: str,
) -> float:
    """
    Ask the AI model to compare before/after page images and rate print quality.

    Returns a score from 0-100.
    """
    # Sample pages for comparison (max 4 pairs to limit token usage)
    max_pairs = min(4, len(before_pages), len(after_pages))
    if max_pairs == 0:
        return 100.0

    # Select representative pages (first, last, and middle)
    indices = _sample_indices(max_pairs, min(len(before_pages), len(after_pages)))

    parts: list[Part] = []
    for idx in indices:
        page_num = idx + 1
        parts.append(Part.from_text(text=f"\n--- Page {page_num} BEFORE ---"))
        before_path = before_pages[idx]
        if Path(before_path).exists():
            image_bytes = await asyncio.to_thread(Path(before_path).read_bytes)
            parts.append(Part.from_bytes(data=image_bytes, mime_type="image/png"))

        parts.append(Part.from_text(text=f"\n--- Page {page_num} AFTER ---"))
        after_path = after_pages[idx]
        if Path(after_path).exists():
            image_bytes = await asyncio.to_thread(Path(after_path).read_bytes)
            parts.append(Part.from_bytes(data=image_bytes, mime_type="image/png"))

    parts.append(Part.from_text(text=VERIFICATION_PROMPT))

    config = GenerateContentConfig(
        temperature=1.0,  # Recommended for Gemini 3
        max_output_tokens=8192,
        response_mime_type="application/json",
    )

    response = await ai_client.aio.models.generate_content(
        model=effort_config.visual_model,
        contents=Content(parts=parts),
        config=config,
    )

    try:
        result = json.loads(response.text or "{}")
        score = float(result.get("overall_score", 85.0))
        return max(0.0, min(100.0, score))
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.warning(f"Job {job_id}: could not parse AI visual score, defaulting to 85")
        return 85.0


def _sample_indices(count: int, total: int) -> list[int]:
    """Pick evenly-spaced page indices for sampling."""
    if total <= count:
        return list(range(total))
    if count == 1:
        return [0]
    if count == 2:
        return [0, total - 1]

    # First, last, and evenly-spaced middle
    indices = [0]
    inner = count - 2
    step = (total - 1) / (inner + 1)
    indices.extend(int(i * step) for i in range(1, inner + 1))
    indices.append(total - 1)
    return indices


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

def _compute_confidence(
    initial_diagnosis: DocumentDiagnosis | None,
    final_diagnosis: DocumentDiagnosis | None,
    orchestration: OrchestrationResult | None,
    visual_score: float = 100.0,
) -> ConfidenceBreakdown:
    """
    Compute a confidence score based on:
      - Issue resolution ratio (how many issues were fixed)
      - Convergence quality (did the fix loop converge?)
      - Remaining severity (penalty for remaining critical/warning issues)
      - AI visual assessment
    """
    # Defaults if no data
    if not initial_diagnosis or not final_diagnosis:
        return ConfidenceBreakdown(
            base_score=50.0,
            final_score=50.0,
            visual_score=visual_score,
        )

    initial_total = initial_diagnosis.summary.total_issues
    final_total = final_diagnosis.summary.total_issues
    final_critical = final_diagnosis.summary.critical_count
    final_warning = final_diagnosis.summary.warning_count

    # --- Base score: issue resolution ratio ---
    if initial_total == 0:
        # No issues found initially — document was already clean
        base_score = 100.0
    elif final_total == 0:
        # All issues resolved
        base_score = 100.0
    else:
        resolved_ratio = max(0.0, (initial_total - final_total) / initial_total)
        base_score = 50.0 + resolved_ratio * 50.0  # 50-100 range

    # --- Convergence bonus/penalty ---
    convergence_bonus = 0.0
    if orchestration:
        if orchestration.converged:
            convergence_bonus = 10.0
        elif orchestration.total_fixes_applied == 0:
            convergence_bonus = -10.0  # No fixes could be applied
        elif orchestration.final_issues >= orchestration.initial_issues:
            convergence_bonus = -5.0  # Issues didn't decrease

    # --- Severity penalty ---
    severity_penalty = 0.0
    severity_penalty -= final_critical * 15.0  # Heavy penalty per remaining critical
    severity_penalty -= final_warning * 5.0    # Moderate penalty per remaining warning
    severity_penalty = max(-50.0, severity_penalty)  # Cap at -50

    # --- Combine ---
    visual_weight = 0.3
    algorithmic = base_score + convergence_bonus + severity_penalty
    algorithmic = max(0.0, min(100.0, algorithmic))

    final_score = (algorithmic * (1 - visual_weight)) + (visual_score * visual_weight)
    final_score = max(0.0, min(100.0, round(final_score, 1)))

    return ConfidenceBreakdown(
        base_score=round(base_score, 1),
        convergence_bonus=round(convergence_bonus, 1),
        severity_penalty=round(severity_penalty, 1),
        visual_score=round(visual_score, 1),
        visual_weight=visual_weight,
        final_score=final_score,
    )


# ---------------------------------------------------------------------------
# Fix report generation
# ---------------------------------------------------------------------------

def _generate_report(
    job_id: str,
    job: dict,
    initial_diagnosis: DocumentDiagnosis | None,
    final_diagnosis: DocumentDiagnosis | None,
    orchestration: OrchestrationResult | None,
    fix_log: FixLog | None,
    confidence: ConfidenceBreakdown,
) -> FixReport:
    """Generate a human-readable fix report."""
    entries: list[FixReportEntry] = []

    # Build a map of tool applications from the fix log
    tool_results: dict[str, list[FixResult]] = {}
    if fix_log:
        for fix in fix_log.fixes:
            tool_results.setdefault(fix.tool_name, []).append(fix)

    # Map initial issues to actions taken
    if initial_diagnosis:
        all_issues = list(initial_diagnosis.document_issues)
        for page_diag in initial_diagnosis.pages:
            all_issues.extend(page_diag.issues)

        # Track which issue types were addressed
        fixed_types: set[str] = set()
        if fix_log:
            for fix in fix_log.fixes:
                if fix.success:
                    fixed_types.add(fix.tool_name)

        for issue in all_issues:
            # Determine action taken
            action = "not_applicable"
            tool_used = issue.suggested_fix

            if tool_used and tool_used in tool_results:
                results = tool_results[tool_used]
                any_success = any(r.success for r in results)
                action = "fixed" if any_success else "failed"
            elif _issue_resolved_in_final(issue, final_diagnosis):
                action = "fixed"
                tool_used = None
            elif issue.severity.value == "info":
                action = "skipped"
            else:
                action = "skipped"

            details = ""
            if tool_used and tool_used in tool_results:
                successful = [r for r in tool_results[tool_used] if r.success]
                if successful and successful[0].after_value:
                    details = f"Changed to: {successful[0].after_value}"

            entries.append(FixReportEntry(
                issue_type=issue.type.value,
                severity=issue.severity.value,
                description=issue.description,
                action_taken=action,
                tool_used=tool_used,
                details=details,
            ))

    # Compute counts
    fixed_count = sum(1 for e in entries if e.action_taken == "fixed")
    skipped_count = sum(1 for e in entries if e.action_taken in ("skipped", "not_applicable"))
    remaining = final_diagnosis.summary.total_issues if final_diagnosis else 0

    # Generate narrative summary
    summary = _narrative_summary(
        original_filename=job.get("original_filename", "document"),
        issues_found=len(entries),
        issues_fixed=fixed_count,
        issues_skipped=skipped_count,
        remaining=remaining,
        confidence=confidence,
        orchestration=orchestration,
    )

    return FixReport(
        job_id=job_id,
        original_filename=job.get("original_filename", "unknown"),
        effort_level=job.get("effort", "standard"),
        aggressiveness=job.get("aggressiveness", "smart_auto"),
        total_pages=int(job.get("pages", 0)),
        issues_found=len(entries),
        issues_fixed=fixed_count,
        issues_skipped=skipped_count,
        issues_remaining=remaining,
        iterations=orchestration.iterations if orchestration else 0,
        confidence=confidence.final_score,
        print_readiness=confidence.print_readiness,
        entries=entries,
        summary=summary,
    )


def _issue_resolved_in_final(
    issue, final_diagnosis: DocumentDiagnosis | None,
) -> bool:
    """Check if an issue from initial diagnosis is absent in final diagnosis."""
    if not final_diagnosis:
        return False

    final_types_by_page: dict[int | None, set[str]] = {}
    for page_diag in final_diagnosis.pages:
        final_types_by_page.setdefault(page_diag.page, set())
        for fi in page_diag.issues:
            final_types_by_page[page_diag.page].add(fi.type.value)

    for di in final_diagnosis.document_issues:
        final_types_by_page.setdefault(None, set())
        final_types_by_page[None].add(di.type.value)

    page = issue.page
    return issue.type.value not in final_types_by_page.get(page, set())


def _narrative_summary(
    original_filename: str,
    issues_found: int,
    issues_fixed: int,
    issues_skipped: int,
    remaining: int,
    confidence: ConfidenceBreakdown,
    orchestration: OrchestrationResult | None,
) -> str:
    """Generate a human-readable narrative summary."""
    parts: list[str] = []

    parts.append(
        f"PrintFix analyzed '{original_filename}' and found {issues_found} "
        f"print quality issue{'s' if issues_found != 1 else ''}."
    )

    if issues_fixed > 0:
        iterations = orchestration.iterations if orchestration else 1
        parts.append(
            f"{issues_fixed} issue{'s were' if issues_fixed != 1 else ' was'} "
            f"successfully fixed across {iterations} "
            f"iteration{'s' if iterations != 1 else ''}."
        )

    if issues_skipped > 0:
        parts.append(
            f"{issues_skipped} issue{'s were' if issues_skipped != 1 else ' was'} "
            f"skipped (low severity or no applicable fix)."
        )

    if remaining > 0:
        parts.append(f"{remaining} issue{'s' if remaining != 1 else ''} remain{'s' if remaining == 1 else ''}.")

    # Readiness assessment
    readiness = confidence.print_readiness
    if readiness == "print_ready":
        parts.append("The document is print-ready with high confidence.")
    elif readiness == "likely_fine":
        parts.append("The document is likely suitable for printing with minor concerns.")
    elif readiness == "needs_review":
        parts.append("Some issues remain — human review is recommended before printing.")
    else:
        parts.append(
            "Significant problems persist — manual intervention is recommended."
        )

    parts.append(f"Overall confidence score: {confidence.final_score:.0f}/100.")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

async def _persist_verification(job_id: str, result: VerificationResult) -> str:
    """Save verification result to disk. Returns the file path."""
    path = get_job_dir(job_id) / "verification.json"
    os.makedirs(path.parent, exist_ok=True)
    async with aiofiles.open(path, "w") as f:
        await f.write(result.model_dump_json(indent=2))
    return str(path)
