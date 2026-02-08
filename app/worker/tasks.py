"""
Phase 1 worker tasks: ingest, convert, render.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.core.config import settings
from app.core.log import logger
from app.core.rendering import convert_to_pdf, get_pdf_metadata, render_pages
from app.worker.broker import broker
from app.worker.job_state import JobStateManager


@broker.task(task_name="ingest_document")
async def ingest_document(job_id: str, file_path: str, original_filename: str) -> dict:
    """
    Full Phase 1 pipeline for a single job:
      1. Validate file type, extract basic metadata
      2. Convert to PDF via LibreOffice headless (or copy if already PDF)
      3. Render pages to PNG images
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

        # -- Done --
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
        return {
            "job_id": job_id,
            "status": "ingested",
            "pages": metadata["page_count"],
        }

    except Exception as exc:
        logger.error(f"Job {job_id} failed: {exc}")
        await JobStateManager.set_state(job_id, "failed", error=str(exc))
        raise
