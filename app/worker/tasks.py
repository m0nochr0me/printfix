"""
Worker tasks: ingest, convert, render, diagnose.
"""

from __future__ import annotations

import json
import os
from hashlib import blake2s
from pathlib import Path

import aiofiles

from app.core.cache import cache, make_cache_key
from app.core.config import settings
from app.core.effort import EffortConfig, get_effort_config
from app.core.log import logger
from app.core.rendering import convert_to_pdf, get_pdf_metadata, render_pages
from app.core.storage import get_job_dir
from app.diagnosis.merge import merge_diagnoses, merge_diagnoses_ai
from app.diagnosis.structural_docx import analyze_docx
from app.diagnosis.structural_pdf import analyze_pdf
from app.diagnosis.visual import inspect_pages_visually
from app.schema.diagnosis import DiagnosisIssue, DocumentDiagnosis
from app.schema.job import EffortLevel
from app.worker.broker import broker
from app.worker.job_state import JobStateManager


@broker.task(task_name="fix_document")
async def fix_document(job_id: str) -> dict:
    """
    Fix orchestration + verification pipeline:
      1. Transition to fixing state
      2. Run the diagnose→fix→re-diagnose loop
      3. Persist orchestration result
      4. Run verification (before/after comparison, confidence scoring, report)
      5. Auto-approve or mark for review based on confidence threshold
    """
    try:
        await JobStateManager.set_state(job_id, "fixing")
        logger.info(f"Job {job_id}: starting fix orchestration")

        from app.orchestration.orchestrator import run_fix_loop

        result = await run_fix_loop(job_id)

        # Persist orchestration result
        result_path = get_job_dir(job_id) / "orchestration.json"
        os.makedirs(result_path.parent, exist_ok=True)
        async with aiofiles.open(result_path, "w") as f:
            await f.write(result.model_dump_json(indent=2))

        # Transition to verifying and run Phase 5 verification
        await JobStateManager.set_state(
            job_id, "verifying",
            extra={
                "orchestration_path": str(result_path),
                "issues_fixed": str(result.total_fixes_applied),
                "issues_found": str(result.initial_issues),
                "final_issues": str(result.final_issues),
            },
        )

        # Run verification: before/after rendering, confidence scoring, report
        from app.verification import run_verification, AUTO_APPROVE_THRESHOLD

        verification = await run_verification(job_id)
        verification_path = get_job_dir(job_id) / "verification.json"
        confidence = verification.confidence.final_score
        readiness = verification.confidence.print_readiness

        if confidence >= AUTO_APPROVE_THRESHOLD:
            await JobStateManager.set_state(
                job_id, "done",
                extra={
                    "confidence": str(confidence),
                    "print_readiness": readiness,
                    "verification_path": str(verification_path),
                },
            )
        else:
            await JobStateManager.set_state(
                job_id, "needs_review",
                extra={
                    "confidence": str(confidence),
                    "print_readiness": readiness,
                    "verification_path": str(verification_path),
                },
            )

        final_status = "done" if confidence >= AUTO_APPROVE_THRESHOLD else "needs_review"

        logger.info(
            f"Job {job_id}: fix orchestration complete — "
            f"{result.iterations} iterations, "
            f"{result.total_fixes_applied} fixes applied, "
            f"converged={result.converged}, "
            f"confidence={confidence:.1f}, "
            f"status={final_status}"
        )

        return {
            "job_id": job_id,
            "status": final_status,
            "iterations": result.iterations,
            "fixes_applied": result.total_fixes_applied,
            "converged": result.converged,
            "confidence": confidence,
            "print_readiness": readiness,
        }

    except Exception as exc:
        logger.error(f"Job {job_id} fix orchestration failed: {exc}")
        await JobStateManager.set_state(job_id, "failed", error=str(exc))
        raise


@broker.task(task_name="ingest_document")
async def ingest_document(job_id: str, file_path: str, original_filename: str) -> dict:
    """
    Full Phase 1 pipeline for a single job:
      1. Validate file type, extract basic metadata
      2. Convert to PDF via LibreOffice headless (or copy if already PDF)
      3. Render pages to PNG images
      4. Auto-enqueue diagnosis
    Updates job state in Redis as it progresses.
    """
    try:
        # -- Step 1: Ingesting --
        await JobStateManager.set_state(job_id, "ingesting")
        logger.info(f"Job {job_id}: ingesting {original_filename}")

        ext = Path(original_filename).suffix.lower()
        if ext not in settings.ALLOWED_EXTENSIONS:
            raise ValueError(f"Unsupported file extension: {ext}")

        file_size = os.path.getsize(file_path)
        await JobStateManager.set_state(
            job_id, "ingesting",
            extra={"file_type": ext, "file_size_bytes": file_size},
        )

        # -- Step 2: Convert to PDF --
        await JobStateManager.set_state(job_id, "converting")
        logger.info(f"Job {job_id}: converting to PDF")
        pdf_path = await convert_to_pdf(file_path, job_id)

        # -- Step 3: Render pages to images --
        await JobStateManager.set_state(job_id, "rendering")
        logger.info(f"Job {job_id}: rendering pages")
        page_images = await render_pages(pdf_path, job_id)
        metadata = await get_pdf_metadata(pdf_path)

        # -- Done ingesting --
        await JobStateManager.set_state(
            job_id, "ingested",
            extra={
                "pdf_path": pdf_path,
                "pages": metadata["page_count"],
                "page_images": page_images,
                "metadata": metadata,
            },
        )

        logger.info(
            f"Job {job_id}: ingestion complete — "
            f"{metadata['page_count']} pages rendered"
        )

        # -- Step 4: Auto-enqueue diagnosis --
        await diagnose_document.kiq(job_id=job_id)

        return {
            "job_id": job_id,
            "status": "ingested",
            "pages": metadata["page_count"],
        }

    except Exception as exc:
        logger.error(f"Job {job_id} failed: {exc}")
        await JobStateManager.set_state(job_id, "failed", error=str(exc))
        raise


@broker.task(task_name="diagnose_document")
async def diagnose_document(job_id: str) -> dict:
    """
    Phase 2 diagnosis pipeline:
      1. Check cache for existing diagnosis
      2. Run visual inspection (Gemini)
      3. Run structural analysis (format-specific)
      4. Merge and deduplicate findings
      5. Store results and update job state
    """
    try:
        await JobStateManager.set_state(job_id, "diagnosing")
        job = await JobStateManager.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        effort = EffortLevel(job.get("effort", "standard"))
        effort_config = get_effort_config(effort)
        file_type = job.get("file_type", ".pdf")
        page_images = json.loads(job.get("page_images", "[]"))
        pdf_path = job.get("pdf_path", "")
        page_count = int(job.get("pages", 0))

        # -- Step 1: Check cache --
        file_hash = _compute_file_hash(job_id)
        cache_key = make_cache_key(file_hash, str(effort))
        cached_data = await cache.get(cache_key)
        if cached_data:
            logger.info(f"Job {job_id}: diagnosis cache hit")
            diagnosis = DocumentDiagnosis.model_validate(cached_data)
            # Update job_id in case it differs from cached version
            diagnosis.job_id = job_id
            await _store_diagnosis(job_id, diagnosis, cached=True)
            return {"job_id": job_id, "status": "diagnosed", "cached": True}

        # -- Step 2: Visual inspection --
        logger.info(
            f"Job {job_id}: starting visual inspection "
            f"({effort_config.visual_model})"
        )
        visual_pages = await inspect_pages_visually(
            page_image_paths=page_images,
            effort_config=effort_config,
            file_type=file_type,
            job_id=job_id,
        )

        # -- Step 3: Structural analysis --
        logger.info(f"Job {job_id}: starting structural analysis")
        structural_issues = await _run_structural_analysis(
            job_id=job_id,
            file_type=file_type,
            pdf_path=pdf_path,
            effort_config=effort_config,
        )

        # -- Step 4: Merge --
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

        # -- Step 5: Store and transition --
        await _store_diagnosis(job_id, diagnosis, cached=False)

        # Cache the result for future identical uploads
        await cache.set(
            cache_key,
            json.loads(diagnosis.model_dump_json()),
            ttl=settings.CACHE_TTL_LONG,
        )

        logger.info(
            f"Job {job_id}: diagnosis complete — "
            f"{diagnosis.summary.total_issues} issues found "
            f"({diagnosis.summary.critical_count} critical)"
        )

        # Auto-enqueue fix orchestration if issues were found
        if diagnosis.summary.total_issues > 0:
            await fix_document.kiq(job_id=job_id)
        else:
            # No issues — skip fixing, mark as done
            await JobStateManager.set_state(
                job_id, "fixing",
                extra={"issues_found": "0"},
            )
            await JobStateManager.set_state(
                job_id, "verifying",
                extra={"confidence": "100.0"},
            )
            await JobStateManager.set_state(job_id, "done")

        return {
            "job_id": job_id,
            "status": "diagnosed",
            "total_issues": diagnosis.summary.total_issues,
        }

    except Exception as exc:
        logger.error(f"Job {job_id} diagnosis failed: {exc}")
        await JobStateManager.set_state(job_id, "failed", error=str(exc))
        raise


async def _run_structural_analysis(
    job_id: str,
    file_type: str,
    pdf_path: str,
    effort_config: EffortConfig,
) -> list[DiagnosisIssue]:
    """Route structural analysis to the correct parser based on file type."""
    issues: list[DiagnosisIssue] = []

    # Always analyze the reference PDF
    if pdf_path:
        issues.extend(await analyze_pdf(pdf_path, job_id))

    # Additionally analyze original format if supported
    original_dir = get_job_dir(job_id) / "original"
    if file_type == ".docx":
        docx_files = list(original_dir.glob("*.docx"))
        if docx_files:
            issues.extend(await analyze_docx(str(docx_files[0]), job_id))

    # For Thorough: Claude structural review
    if effort_config.use_claude_structural and issues:
        issues = await _claude_structural_review(issues, effort_config, job_id)

    return issues


async def _claude_structural_review(
    issues: list[DiagnosisIssue],
    effort_config: EffortConfig,
    job_id: str,
) -> list[DiagnosisIssue]:
    """Use Claude to review and refine structural findings (Thorough only)."""
    import asyncio

    from app.core.ai import extract_anthropic_text, get_anthropic_client
    from app.core.prompts import STRUCTURAL_REVIEW_PROMPT

    client = get_anthropic_client()
    if not client:
        logger.warning(
            f"Job {job_id}: Anthropic client not configured, "
            "skipping Claude structural review"
        )
        return issues

    issues_json = json.dumps([i.model_dump() for i in issues], indent=2)
    model = effort_config.claude_model or settings.ANTHROPIC_DIAGNOSIS_MODEL

    try:
        prompt = STRUCTURAL_REVIEW_PROMPT.format(
            file_type="document",
            structural_data=issues_json,
        )
        response = await asyncio.to_thread(
            client.messages.create,
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_text = extract_anthropic_text(response)
        data = json.loads(raw_text)

        reviewed: list[DiagnosisIssue] = []
        for item in data.get("reviewed_issues", []):
            try:
                from app.schema.diagnosis import IssueSeverity, IssueSource, IssueType
                reviewed.append(DiagnosisIssue(
                    type=IssueType(item["type"]),
                    severity=IssueSeverity(item.get("severity", "warning")),
                    source=IssueSource.structural,
                    page=item.get("page"),
                    location=item.get("location"),
                    description=item.get("description", ""),
                    suggested_fix=item.get("suggested_fix"),
                    confidence=max(0.0, min(1.0, float(item.get("confidence", 0.7)))),
                ))
            except (ValueError, KeyError):
                continue

        return reviewed if reviewed else issues

    except Exception:
        logger.exception(
            f"Job {job_id}: Claude structural review failed, "
            "keeping original findings"
        )
        return issues


def _compute_file_hash(job_id: str) -> str:
    """Compute a content hash of the original uploaded file for cache keying."""
    original_dir = get_job_dir(job_id) / "original"
    files = list(original_dir.iterdir())
    if not files:
        return "unknown"
    h = blake2s()
    h.update(files[0].read_bytes())
    return h.hexdigest()


async def _store_diagnosis(
    job_id: str,
    diagnosis: DocumentDiagnosis,
    cached: bool,
) -> None:
    """Save diagnosis to disk and update Redis job hash."""
    diag_path = get_job_dir(job_id) / "diagnosis.json"
    os.makedirs(diag_path.parent, exist_ok=True)

    async with aiofiles.open(diag_path, "w") as f:
        await f.write(diagnosis.model_dump_json(indent=2))

    await JobStateManager.set_state(
        job_id, "diagnosed",
        extra={
            "issues_found": str(diagnosis.summary.total_issues),
            "print_readiness": diagnosis.summary.print_readiness,
            "diagnosis_path": str(diag_path),
            "diagnosis_cached": str(cached).lower(),
        },
    )
