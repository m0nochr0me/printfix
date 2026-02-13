"""
REST API router — Job CRUD endpoints.
"""

import asyncio
import json
from pathlib import Path
from random import random

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from ulid import ULID

from app.api.deps import check_rate_limit, verify_token
from app.core.config import settings
from app.core.storage import delete_job_files, get_job_dir, save_upload
from app.fixes.common import get_fix_log
from app.schema.diagnosis import DiagnosisResponse
from app.schema.fix import FixLog
from app.schema.job import (
    Aggressiveness,
    ColorSpace,
    EffortLevel,
    JobCreateResponse,
    JobPreviewResponse,
    JobResponse,
    JobStatus,
    PageSize,
)
from app.schema.orchestration import OrchestrationResponse, OrchestrationResult
from app.worker.job_state import JobStateManager
from app.worker.tasks import diagnose_document, fix_document, ingest_document

__all__ = ("router",)

router = APIRouter(
    prefix="/v1",
    tags=["jobs"],
    dependencies=[Depends(verify_token), Depends(check_rate_limit)],
)


@router.get("/auth", tags=["Management"])
async def auth() -> dict[str, str]:
    """
    Simple authentication endpoint to verify API key validity.
    """
    await asyncio.sleep(2 + random() * 2)

    return {"status": "authorized"}


@router.get("/jobs")
async def list_jobs() -> list[JobResponse]:
    """List all jobs, sorted by creation time descending."""
    raw_jobs = await JobStateManager.list_jobs()
    results: list[JobResponse] = []
    for j in raw_jobs:
        try:
            results.append(
                JobResponse(
                    id=j["id"],
                    status=JobStatus(j["status"]),
                    effort=EffortLevel(j.get("effort", "standard")),
                    aggressiveness=Aggressiveness(j.get("aggressiveness", "smart_auto")),
                    original_filename=j.get("original_filename", "unknown"),
                    file_type=j.get("file_type"),
                    file_size_bytes=int(j["file_size_bytes"]) if j.get("file_size_bytes") else None,
                    pages=int(j["pages"]) if j.get("pages") else None,
                    issues_found=int(j.get("issues_found", 0)),
                    issues_fixed=int(j.get("issues_fixed", 0)),
                    issues_skipped=int(j.get("issues_skipped", 0)),
                    confidence=float(j["confidence"]) if j.get("confidence") else None,
                    print_readiness=j.get("print_readiness"),
                    created_at=j["created_at"],
                    updated_at=j["updated_at"],
                    completed_at=j.get("completed_at"),
                    error=j.get("error"),
                )
            )
        except (KeyError, ValueError):
            continue
    return results


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    file: UploadFile = File(...),
    effort: EffortLevel = Form(default=EffortLevel.standard),
    aggressiveness: Aggressiveness = Form(default=Aggressiveness.smart_auto),
    target_page_size: PageSize | None = Form(default=None),
    target_colorspace: ColorSpace | None = Form(default=None),
) -> JobCreateResponse:
    """Upload a document and create a processing job."""
    filename = file.filename or "unknown"
    ext = Path(filename).suffix.lower()

    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type: {ext}. Allowed: {', '.join(settings.ALLOWED_EXTENSIONS)}",
        )

    content = await file.read()
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum size: {settings.MAX_UPLOAD_SIZE_MB} MB",
        )

    job_id = str(ULID())
    file_path = await save_upload(job_id, filename, content)

    extra: dict[str, str] = {
        "effort": str(effort),
        "aggressiveness": str(aggressiveness),
    }
    if target_page_size:
        extra["target_page_size"] = str(target_page_size)
    if target_colorspace:
        extra["target_colorspace"] = str(target_colorspace)

    await JobStateManager.create_job(
        job_id,
        original_filename=filename,
        **extra,
    )

    await ingest_document.kiq(
        job_id=job_id,
        file_path=file_path,
        original_filename=filename,
    )

    job = await JobStateManager.get_job(job_id)
    return JobCreateResponse(
        id=job_id,
        status=JobStatus.uploaded,
        original_filename=filename,
        created_at=job["created_at"],  # type: ignore[index]
    )


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> JobResponse:
    """Get the current status and details of a job."""
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobResponse(
        id=job["id"],
        status=JobStatus(job["status"]),
        effort=EffortLevel(job.get("effort", "standard")),
        aggressiveness=Aggressiveness(job.get("aggressiveness", "smart_auto")),
        original_filename=job["original_filename"],
        file_type=job.get("file_type"),
        file_size_bytes=int(job["file_size_bytes"]) if job.get("file_size_bytes") else None,
        pages=int(job["pages"]) if job.get("pages") else None,
        issues_found=int(job.get("issues_found", 0)),
        issues_fixed=int(job.get("issues_fixed", 0)),
        issues_skipped=int(job.get("issues_skipped", 0)),
        confidence=float(job["confidence"]) if job.get("confidence") else None,
        print_readiness=job.get("print_readiness"),
        created_at=job["created_at"],
        updated_at=job["updated_at"],
        completed_at=job.get("completed_at"),
        error=job.get("error"),
    )


@router.get("/jobs/{job_id}/preview")
async def get_preview(job_id: str) -> JobPreviewResponse:
    """Get the list of rendered page image paths for a job."""
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] in ("uploaded", "ingesting", "converting", "rendering"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Pages not yet rendered. Current status: {job['status']}",
        )

    page_images_raw = job.get("page_images", "[]")
    page_images = json.loads(page_images_raw) if isinstance(page_images_raw, str) else page_images_raw

    return JobPreviewResponse(
        job_id=job_id,
        pages=page_images,
        page_count=int(job.get("pages", 0)),
    )


@router.get("/jobs/{job_id}/preview/{page}")
async def get_preview_page(job_id: str, page: int) -> FileResponse:
    """Get a single rendered page image."""
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    page_images_raw = job.get("page_images", "[]")
    page_images = json.loads(page_images_raw) if isinstance(page_images_raw, str) else page_images_raw

    if page < 1 or page > len(page_images):
        raise HTTPException(status_code=404, detail=f"Page {page} not found")

    image_path = page_images[page - 1]
    if not Path(image_path).exists():
        raise HTTPException(status_code=404, detail="Page image file not found")

    return FileResponse(image_path, media_type="image/png")


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: str) -> None:
    """Delete a job and all associated files."""
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    await JobStateManager.delete_job(job_id)
    delete_job_files(job_id)


# -- Stubs for later phases --


@router.get("/jobs/{job_id}/diagnosis")
async def get_diagnosis(job_id: str) -> DiagnosisResponse:
    """Get the full diagnosis detail for a job."""
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    pre_diagnosis_states = (
        "uploaded",
        "ingesting",
        "converting",
        "rendering",
        "ingested",
    )
    if job["status"] in pre_diagnosis_states:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Diagnosis not yet available. Current status: {job['status']}",
        )

    if job["status"] == "diagnosing":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Diagnosis is currently in progress",
        )

    diagnosis_path = job.get("diagnosis_path")
    if not diagnosis_path or not Path(diagnosis_path).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diagnosis results not found on disk",
        )


    async with aiofiles.open(diagnosis_path, "r") as f:
        diagnosis_data = json.loads(await f.read())

    return DiagnosisResponse(
        job_id=job_id,
        status=job["status"],
        diagnosis=diagnosis_data,
        cached=job.get("diagnosis_cached", "false") == "true",
    )


@router.post("/jobs/{job_id}/diagnose", status_code=status.HTTP_202_ACCEPTED)
async def trigger_diagnosis(job_id: str) -> dict:
    """Manually trigger or re-trigger diagnosis for a job."""
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] not in ("ingested", "diagnosed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot diagnose from status: {job['status']}",
        )

    await diagnose_document.kiq(job_id=job_id)
    return {"job_id": job_id, "message": "Diagnosis started"}


@router.get("/jobs/{job_id}/fixes")
async def get_fixes(job_id: str) -> FixLog:
    """Get the list of fixes applied to a job."""
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")


    return await get_fix_log(job_id)


@router.post("/jobs/{job_id}/fix", status_code=status.HTTP_202_ACCEPTED)
async def trigger_fix(job_id: str) -> dict:
    """Manually trigger fix orchestration for a diagnosed job."""
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] not in ("diagnosed", "needs_review"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot start fixing from status: {job['status']}. "
            f"Job must be in 'diagnosed' or 'needs_review' state.",
        )

    await fix_document.kiq(job_id=job_id)
    return {"job_id": job_id, "message": "Fix orchestration started"}


@router.get("/jobs/{job_id}/orchestration")
async def get_orchestration(job_id: str) -> OrchestrationResponse:
    """Get the orchestration result for a job (fix loop summary)."""
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    pre_fix_states = (
        "uploaded",
        "ingesting",
        "converting",
        "rendering",
        "ingested",
        "diagnosing",
        "diagnosed",
    )
    if job["status"] in pre_fix_states:
        return OrchestrationResponse(
            job_id=job_id,
            status=job["status"],
            result=None,
        )

    orchestration_path = job.get("orchestration_path")
    if orchestration_path and Path(orchestration_path).exists():

        async with aiofiles.open(orchestration_path, "r") as f:
            result_data = json.loads(await f.read())
        return OrchestrationResponse(
            job_id=job_id,
            status=job["status"],
            result=OrchestrationResult.model_validate(result_data),
        )

    # Fixing is in progress or result not yet persisted
    if job["status"] == "fixing":
        return OrchestrationResponse(
            job_id=job_id,
            status="fixing",
            result=None,
        )

    return OrchestrationResponse(
        job_id=job_id,
        status=job["status"],
        result=None,
    )


@router.post("/jobs/{job_id}/approve")
async def approve_job(job_id: str) -> dict:
    """Approve a job result, marking it as done."""
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] not in ("needs_review", "verifying"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot approve from status: {job['status']}. Job must be in 'needs_review' or 'verifying' state.",
        )

    await JobStateManager.set_state(
        job_id,
        "done",
        extra={"manually_approved": "true"},
    )
    return {"job_id": job_id, "status": "done", "message": "Job approved"}


@router.post("/jobs/{job_id}/reject")
async def reject_job(job_id: str) -> dict:
    """Reject a job result, marking it for re-processing or manual intervention."""
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] not in ("done", "needs_review", "verifying"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot reject from status: {job['status']}. "
            f"Job must be in 'done', 'needs_review', or 'verifying' state.",
        )

    await JobStateManager.set_state(
        job_id,
        "needs_review",
        extra={"rejected": "true"},
    )
    return {"job_id": job_id, "status": "needs_review", "message": "Job rejected for review"}


@router.get("/jobs/{job_id}/download")
async def download_job(
    job_id: str,
    format: str = "pdf",
) -> FileResponse:
    """
    Download the fixed file.

    Query params:
      - format: "pdf" (default) or "original" (returns the fixed original-format file)
    """
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] not in ("done", "needs_review"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot download from status: {job['status']}. Job must be in 'done' or 'needs_review' state.",
        )


    job_dir = get_job_dir(job_id)
    original_filename = job.get("original_filename", "document")
    file_type = job.get("file_type", ".pdf")

    if format == "original" and file_type != ".pdf":
        # Return the fixed original-format file
        original_dir = job_dir / "original"
        matches = list(original_dir.glob(f"*{file_type}"))
        if not matches:
            raise HTTPException(
                status_code=404,
                detail=f"Original format file ({file_type}) not found",
            )
        return FileResponse(
            str(matches[0]),
            media_type="application/octet-stream",
            filename=original_filename,
        )

    # Default: return the reference PDF
    pdf_path = job.get("pdf_path", "")
    if not pdf_path or not Path(pdf_path).exists():
        raise HTTPException(status_code=404, detail="PDF file not found")

    stem = Path(original_filename).stem
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=f"{stem}_printfix.pdf",
    )


@router.get("/jobs/{job_id}/verification")
async def get_verification(job_id: str) -> dict:
    """Get the verification result including confidence score and fix report."""
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    verification_path = job.get("verification_path")
    if not verification_path or not Path(verification_path).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Verification results not available yet",
        )

    async with aiofiles.open(verification_path, "r") as f:
        verification_data = json.loads(await f.read())

    return {
        "job_id": job_id,
        "status": job["status"],
        "verification": verification_data,
    }


@router.get("/jobs/{job_id}/report")
async def get_report(job_id: str) -> dict:
    """Get the human-readable fix report."""
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    verification_path = job.get("verification_path")
    if not verification_path or not Path(verification_path).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Fix report not available yet",
        )

    async with aiofiles.open(verification_path, "r") as f:
        verification_data = json.loads(await f.read())

    report = verification_data.get("report")
    if not report:
        raise HTTPException(status_code=404, detail="Report not found in verification data")

    return {
        "job_id": job_id,
        "report": report,
    }


@router.get("/jobs/{job_id}/preview/comparison")
async def get_preview_comparison(job_id: str) -> dict:
    """Get before/after page comparison data."""
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    verification_path = job.get("verification_path")
    if not verification_path or not Path(verification_path).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comparison data not available yet",
        )

    async with aiofiles.open(verification_path, "r") as f:
        verification_data = json.loads(await f.read())

    comparisons = verification_data.get("page_comparisons", [])
    return {
        "job_id": job_id,
        "page_comparisons": comparisons,
        "page_count": len(comparisons),
    }


@router.get("/jobs/{job_id}/preview/before/{page}")
async def get_preview_before(job_id: str, page: int) -> FileResponse:
    """Get the before (pre-fix) page image."""
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    before_dir = get_job_dir(job_id) / "pages_before"
    if not before_dir.exists():
        before_dir = get_job_dir(job_id) / "pages"

    image_path = before_dir / f"{page}.png"
    if not image_path.exists():
        raise HTTPException(status_code=404, detail=f"Before page {page} not found")

    return FileResponse(str(image_path), media_type="image/png")


@router.get("/jobs/{job_id}/preview/after/{page}")
async def get_preview_after(job_id: str, page: int) -> FileResponse:
    """Get the after (post-fix) page image."""
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    after_dir = get_job_dir(job_id) / "pages_after"
    if not after_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="After-fix page images not available yet",
        )

    image_path = after_dir / f"{page}.png"
    if not image_path.exists():
        raise HTTPException(status_code=404, detail=f"After page {page} not found")

    return FileResponse(str(image_path), media_type="image/png")
