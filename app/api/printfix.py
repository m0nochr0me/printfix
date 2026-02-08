"""
REST API router — Job CRUD endpoints.
"""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from ulid import ULID

from app.api.deps import verify_token
from app.core.config import settings
from app.core.storage import delete_job_files, save_upload
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
from app.schema.diagnosis import DiagnosisResponse
from app.worker.job_state import JobStateManager
from app.worker.tasks import diagnose_document, ingest_document

__all__ = ("router",)

router = APIRouter(
    prefix="/v1",
    tags=["jobs"],
    dependencies=[Depends(verify_token)],
)


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
        "uploaded", "ingesting", "converting", "rendering", "ingested",
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

    import aiofiles
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
async def get_fixes(job_id: str) -> dict:
    """Get the list of fixes applied to a job. (Phase 3)"""
    raise HTTPException(status_code=501, detail="Not implemented yet — coming in Phase 3")


@router.post("/jobs/{job_id}/approve")
async def approve_job(job_id: str) -> dict:
    """Approve a job result. (Phase 5)"""
    raise HTTPException(status_code=501, detail="Not implemented yet — coming in Phase 5")


@router.post("/jobs/{job_id}/reject")
async def reject_job(job_id: str) -> dict:
    """Reject a job result. (Phase 5)"""
    raise HTTPException(status_code=501, detail="Not implemented yet — coming in Phase 5")


@router.get("/jobs/{job_id}/download")
async def download_job(job_id: str) -> dict:
    """Download the fixed file. (Phase 5)"""
    raise HTTPException(status_code=501, detail="Not implemented yet — coming in Phase 5")
