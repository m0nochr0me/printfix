"""
Local disk file storage for job artifacts.
"""

import os
import shutil
from pathlib import Path

import aiofiles

from app.core.config import settings

__all__ = (
    "delete_job_files",
    "get_job_dir",
    "save_pdf",
    "save_rendered_page",
    "save_upload",
)


def get_job_dir(job_id: str) -> Path:
    return Path(settings.STORAGE_DIR) / "jobs" / job_id


async def save_upload(job_id: str, filename: str, content: bytes) -> str:
    """Save an uploaded file and return its path."""
    job_dir = get_job_dir(job_id) / "original"
    os.makedirs(job_dir, exist_ok=True)
    file_path = job_dir / filename
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)
    return str(file_path)


async def save_pdf(job_id: str, pdf_bytes: bytes, name: str = "reference.pdf") -> str:
    """Save a reference PDF and return its path."""
    job_dir = get_job_dir(job_id) / "pdf"
    os.makedirs(job_dir, exist_ok=True)
    file_path = job_dir / name
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(pdf_bytes)
    return str(file_path)


async def save_rendered_page(job_id: str, page_num: int, image_bytes: bytes) -> str:
    """Save a rendered page image and return its path."""
    job_dir = get_job_dir(job_id) / "pages"
    os.makedirs(job_dir, exist_ok=True)
    file_path = job_dir / f"{page_num}.png"
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(image_bytes)
    return str(file_path)


def delete_job_files(job_id: str) -> None:
    """Remove all files for a job."""
    job_dir = get_job_dir(job_id)
    if job_dir.exists():
        shutil.rmtree(job_dir)
