"""
Fix orchestrator — the main diagnose→fix→re-diagnose loop.
"""

from __future__ import annotations

import json
import os

import aiofiles

from app.core.effort import EffortConfig, get_effort_config
from app.core.log import logger
from app.core.storage import get_job_dir
from app.diagnosis.merge import merge_diagnoses, merge_diagnoses_ai
from app.diagnosis.structural_pdf import analyze_pdf
from app.diagnosis.visual import inspect_pages_visually
from app.orchestration.convergence import should_stop
from app.orchestration.executor import execute_plan
from app.orchestration.planner import plan_fixes
from app.schema.diagnosis import (
    DiagnosisIssue,
    DocumentDiagnosis,
    IssueSeverity,
)
from app.schema.job import EffortLevel
from app.schema.orchestration import ConvergenceState, OrchestrationResult
from app.worker.job_state import JobStateManager

__all__ = ("run_fix_loop",)


async def run_fix_loop(job_id: str) -> OrchestrationResult:
    """
    Main orchestration entry point.

    Reads diagnosis, selects fixes, applies them, re-diagnoses,
    and loops until convergence or max iterations.
    """
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    effort = EffortLevel(job.get("effort", "standard"))
    effort_config = get_effort_config(effort)
    aggressiveness = job.get("aggressiveness", "smart_auto")
    file_type = job.get("file_type", ".pdf")
    target_page_size = job.get("target_page_size")

    # Load initial diagnosis
    diagnosis = await _load_diagnosis(job_id)
    initial_issues = diagnosis.summary.total_issues
    initial_critical = diagnosis.summary.critical_count

    # Snapshot initial state for Phase 5 verification (before/after comparison)
    await _snapshot_initial_state(job_id, diagnosis)

    logger.info(
        f"Job {job_id}: starting fix loop — "
        f"{initial_issues} issues ({initial_critical} critical), "
        f"effort={effort}, aggressiveness={aggressiveness}, "
        f"max_iterations={effort_config.max_fix_iterations}"
    )

    convergence_history: list[ConvergenceState] = []
    total_applied = 0
    total_failed = 0
    any_fallback_used = False

    # Accumulated set of issue types whose DOCX fixes failed across iterations.
    # Passed to the planner so it can route these to PDF fallback tools.
    cumulative_failed_issue_types: set[str] = set()

    for iteration in range(1, effort_config.max_fix_iterations + 1):
        issues_before = diagnosis.summary.total_issues
        critical_before = diagnosis.summary.critical_count
        warning_before = diagnosis.summary.warning_count

        logger.info(
            f"Job {job_id}: iteration {iteration}/{effort_config.max_fix_iterations} — "
            f"{issues_before} issues ({critical_before} critical)"
            + (
                f", fallback candidates: {cumulative_failed_issue_types}"
                if cumulative_failed_issue_types
                else ""
            )
        )

        # 1. Plan fixes (pass failed issue types for PDF fallback routing)
        plan = await plan_fixes(
            diagnosis=diagnosis,
            aggressiveness=aggressiveness,
            file_type=file_type,
            effort_config=effort_config,
            target_page_size=target_page_size,
            iteration=iteration,
            failed_issue_types=cumulative_failed_issue_types,
        )

        if not plan.actions:
            logger.info(f"Job {job_id}: no applicable fixes in iteration {iteration}")
            state = ConvergenceState(
                iteration=iteration,
                issues_before=issues_before,
                issues_after=issues_before,
                critical_before=critical_before,
                critical_after=critical_before,
                warning_before=warning_before,
                warning_after=warning_before,
                fixes_applied=0,
                fixes_failed=0,
            )
            convergence_history.append(state)
            break

        has_fallback_actions = any(a.is_fallback for a in plan.actions)

        logger.info(
            f"Job {job_id}: planned {len(plan.actions)} fixes"
            f"{' (includes PDF fallbacks)' if has_fallback_actions else ''}, "
            f"skipped {len(plan.skipped_issues)} issues"
        )

        # 2. Execute fixes
        applied, failed, iter_failed_issues, iter_used_fallback = await execute_plan(
            job_id, plan.actions,
        )
        total_applied += applied
        total_failed += failed
        cumulative_failed_issue_types.update(iter_failed_issues)
        if iter_used_fallback:
            any_fallback_used = True

        logger.info(
            f"Job {job_id}: iteration {iteration} — "
            f"{applied} applied, {failed} failed"
            + (f", used PDF fallback" if iter_used_fallback else "")
        )

        # 3. Re-diagnose to evaluate remaining issues
        diagnosis = await _run_diagnosis(job_id, effort, effort_config, file_type)

        issues_after = diagnosis.summary.total_issues
        critical_after = diagnosis.summary.critical_count
        warning_after = diagnosis.summary.warning_count
        info_after = issues_after - critical_after - warning_after

        logger.info(
            f"Job {job_id}: after iteration {iteration} — "
            f"{issues_after} total ({critical_after} critical, {warning_after} warning, {info_after} info)"
        )

        # 4. Record convergence state
        state = ConvergenceState(
            iteration=iteration,
            issues_before=issues_before,
            issues_after=issues_after,
            critical_before=critical_before,
            critical_after=critical_after,
            warning_before=warning_before,
            warning_after=warning_after,
            fixes_applied=applied,
            fixes_failed=failed,
            used_fallback=iter_used_fallback,
        )
        convergence_history.append(state)

        # 5. Check stopping condition (with fallback awareness)
        fallback_available = _has_untried_fallback(
            cumulative_failed_issue_types, convergence_history, file_type,
        )
        if fallback_available:
            logger.info(
                f"Job {job_id}: PDF fallback available for: {cumulative_failed_issue_types}"
            )
        stop, reason = should_stop(
            convergence_history,
            effort_config.max_fix_iterations,
            fallback_available=fallback_available,
        )
        if stop:
            logger.info(
                f"Job {job_id}: stopping — {reason}"
                + (f" (fallback was available)" if fallback_available else "")
            )
            break

    final_issues = diagnosis.summary.total_issues
    final_critical = diagnosis.summary.critical_count
    converged = final_critical == 0 and diagnosis.summary.warning_count == 0

    # Persist final diagnosis
    await _store_diagnosis(job_id, diagnosis)

    result = OrchestrationResult(
        job_id=job_id,
        iterations=len(convergence_history),
        total_fixes_applied=total_applied,
        total_fixes_failed=total_failed,
        initial_issues=initial_issues,
        final_issues=final_issues,
        initial_critical=initial_critical,
        final_critical=final_critical,
        converged=converged,
        used_fallback=any_fallback_used,
        stop_reason=convergence_history[-1].iteration >= effort_config.max_fix_iterations
        and not converged
        and "max iterations reached"
        or (
            "print-ready" if converged else
            should_stop(convergence_history, effort_config.max_fix_iterations)[1]
        ),
    )

    logger.info(
        f"Job {job_id}: fix loop complete — "
        f"{result.iterations} iterations, "
        f"{total_applied} fixes applied, "
        f"{initial_issues} → {final_issues} issues, "
        f"converged={converged}"
        + (", used PDF fallback" if any_fallback_used else "")
    )

    return result


def _has_untried_fallback(
    failed_issue_types: set[str],
    convergence_history: list[ConvergenceState],
    file_type: str,
) -> bool:
    """
    Check if there are untried PDF fallback tools available.

    Returns True if:
    - The document is an editable format (DOCX, etc.)
    - There are failed issue types that have PDF fallback mappings
    - No iteration has yet used fallback tools
    """
    from app.orchestration.planner import _DOCX_TO_PDF_FALLBACK, _is_editable_format

    if not _is_editable_format(file_type):
        return False

    if not failed_issue_types:
        return False

    # Check if any previous iteration already used fallback
    if any(s.used_fallback for s in convergence_history):
        return False

    # Check if any failed issue type has a PDF fallback available
    return bool(failed_issue_types & set(_DOCX_TO_PDF_FALLBACK.keys()))


async def _load_diagnosis(job_id: str) -> DocumentDiagnosis:
    """Load diagnosis from disk."""
    diag_path = get_job_dir(job_id) / "diagnosis.json"
    if not diag_path.exists():
        raise FileNotFoundError(f"No diagnosis found for job {job_id}")

    async with aiofiles.open(diag_path, "r") as f:
        data = json.loads(await f.read())
    return DocumentDiagnosis.model_validate(data)


async def _run_diagnosis(
    job_id: str,
    effort: EffortLevel,
    effort_config: EffortConfig,
    file_type: str,
) -> DocumentDiagnosis:
    """
    Re-run diagnosis inline (without changing job state or caching).

    This is a lightweight version of the diagnose_document task,
    used within the fix loop for re-evaluation.
    """
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    page_images = json.loads(job.get("page_images", "[]"))
    pdf_path = job.get("pdf_path", "")
    page_count = int(job.get("pages", 0))

    # Visual inspection
    visual_pages = await inspect_pages_visually(
        page_image_paths=page_images,
        effort_config=effort_config,
        file_type=file_type,
        job_id=job_id,
    )

    # Structural analysis (PDF only — fast, no DOCX re-analysis needed)
    structural_issues: list[DiagnosisIssue] = []
    if pdf_path:
        structural_issues = await analyze_pdf(pdf_path, job_id)

    # Additionally analyze original format if applicable
    original_dir = get_job_dir(job_id) / "original"
    if file_type == ".docx":
        from app.diagnosis.structural_docx import analyze_docx

        docx_files = list(original_dir.glob("*.docx"))
        if docx_files:
            structural_issues.extend(await analyze_docx(str(docx_files[0]), job_id))
    elif file_type == ".xlsx":
        from app.diagnosis.structural_xlsx import analyze_xlsx

        xlsx_files = list(original_dir.glob("*.xlsx"))
        if xlsx_files:
            structural_issues.extend(await analyze_xlsx(str(xlsx_files[0]), job_id))
    elif file_type == ".pptx":
        from app.diagnosis.structural_pptx import analyze_pptx

        pptx_files = list(original_dir.glob("*.pptx"))
        if pptx_files:
            structural_issues.extend(await analyze_pptx(str(pptx_files[0]), job_id))

    # Merge (rule-based within fix loop — skip expensive AI merge)
    if effort_config.use_ai_merge:
        diagnosis = await merge_diagnoses_ai(
            visual_pages, structural_issues,
            job_id, str(effort), file_type, page_count, effort_config,
        )
    else:
        diagnosis = merge_diagnoses(
            visual_pages, structural_issues,
            job_id, str(effort), file_type, page_count,
        )

    return diagnosis


async def _store_diagnosis(job_id: str, diagnosis: DocumentDiagnosis) -> None:
    """Persist updated diagnosis to disk and update Redis summary."""
    diag_path = get_job_dir(job_id) / "diagnosis.json"
    os.makedirs(diag_path.parent, exist_ok=True)

    async with aiofiles.open(diag_path, "w") as f:
        await f.write(diagnosis.model_dump_json(indent=2))

    await JobStateManager.set_state(
        job_id, "fixing",
        extra={
            "issues_found": str(diagnosis.summary.total_issues),
            "print_readiness": diagnosis.summary.print_readiness,
        },
    )


async def _snapshot_initial_state(job_id: str, diagnosis: DocumentDiagnosis) -> None:
    """
    Preserve the pre-fix diagnosis and page images for before/after comparison.

    Saves diagnosis as diagnosis_initial.json and copies pages/ to pages_before/.
    Safe to call multiple times — skips if snapshots already exist.
    """
    import shutil

    job_dir = get_job_dir(job_id)

    # Snapshot initial diagnosis
    initial_diag_path = job_dir / "diagnosis_initial.json"
    if not initial_diag_path.exists():
        async with aiofiles.open(initial_diag_path, "w") as f:
            await f.write(diagnosis.model_dump_json(indent=2))
        logger.debug(f"Job {job_id}: saved initial diagnosis snapshot")

    # Snapshot before-pages
    pages_dir = job_dir / "pages"
    before_dir = job_dir / "pages_before"
    if pages_dir.exists() and not before_dir.exists():
        shutil.copytree(pages_dir, before_dir)
        logger.debug(f"Job {job_id}: saved before-pages snapshot")

