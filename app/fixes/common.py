"""Common helpers for fix tools: document resolution, re-rendering, fix logging."""

from __future__ import annotations

import json
import os
from pathlib import Path

import aiofiles

from app.core.log import logger
from app.core.rendering import convert_to_pdf, get_pdf_metadata, render_pages
from app.core.storage import get_job_dir
from app.schema.fix import FixLog, FixResult
from app.worker.job_state import JobStateManager

__all__ = (
    "get_fix_log",
    "re_render_job",
    "record_fix",
    "resolve_document",
)


async def resolve_document(job_id: str) -> tuple[str, str]:
    """
    Resolve a job_id to its document file path and type.

    Returns (file_path, file_type) where file_type is e.g. '.docx', '.pdf'.
    For DOCX jobs, returns the original DOCX path.
    For PDF jobs, returns the reference PDF path.
    """
    job = await JobStateManager.get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    file_type = job.get("file_type", ".pdf")
    original_dir = get_job_dir(job_id) / "original"

    # For editable formats, return the original file
    if file_type in (".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"):
        pattern = f"*{file_type}"
        matches = list(original_dir.glob(pattern))
        if matches:
            return str(matches[0]), file_type

    # Fall back to the reference PDF
    pdf_path = job.get("pdf_path", "")
    if pdf_path and Path(pdf_path).exists():
        return pdf_path, ".pdf"

    raise FileNotFoundError(f"No document found for job {job_id}")


async def re_render_job(job_id: str) -> dict:
    """
    Re-render a job's PDF and page images after a fix has been applied.

    Returns updated metadata dict.
    """
    file_path, file_type = await resolve_document(job_id)

    logger.info(f"Job {job_id}: re-rendering after fix")
    pdf_path = await convert_to_pdf(file_path, job_id)
    page_images = await render_pages(pdf_path, job_id)
    metadata = await get_pdf_metadata(pdf_path)

    await JobStateManager.set_state(
        job_id,
        "fixing",
        extra={
            "pdf_path": pdf_path,
            "pages": metadata["page_count"],
            "page_images": page_images,
            "metadata": metadata,
        },
    )

    return metadata


async def record_fix(job_id: str, result: FixResult) -> None:
    """Append a fix result to the job's fix log on disk."""
    fixes_path = get_job_dir(job_id) / "fixes.json"
    os.makedirs(fixes_path.parent, exist_ok=True)

    # Read existing log
    existing: list[dict] = []
    if fixes_path.exists():
        async with aiofiles.open(fixes_path, "r") as f:
            existing = json.loads(await f.read())

    existing.append(json.loads(result.model_dump_json()))

    async with aiofiles.open(fixes_path, "w") as f:
        await f.write(json.dumps(existing, indent=2))

    # Update counts in Redis
    succeeded = sum(1 for f in existing if f.get("success"))
    failed = sum(1 for f in existing if not f.get("success"))
    await JobStateManager.set_state(
        job_id,
        "fixing",
        extra={
            "issues_fixed": str(succeeded),
            "issues_skipped": str(failed),
        },
    )


async def get_fix_log(job_id: str) -> FixLog:
    """Read the fix log for a job."""
    fixes_path = get_job_dir(job_id) / "fixes.json"

    fixes: list[FixResult] = []
    if fixes_path.exists():
        async with aiofiles.open(fixes_path, "r") as f:
            raw = json.loads(await f.read())
        fixes = [FixResult.model_validate(entry) for entry in raw]

    return FixLog(
        job_id=job_id,
        fixes=fixes,
        total_applied=sum(1 for f in fixes if f.success),
        total_failed=sum(1 for f in fixes if not f.success),
    )
