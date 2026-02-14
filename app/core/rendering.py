"""
Document rendering: LibreOffice headless conversion and PDF page-to-image rendering.
"""

import asyncio
import io
import os
import shutil
from pathlib import Path

import pikepdf
from pdf2image import convert_from_path
from PIL import Image

from app.core.config import settings
from app.core.log import logger
from app.core.storage import get_job_dir, save_rendered_page

__all__ = (
    "convert_to_pdf",
    "get_pdf_metadata",
    "render_pages",
)

_PDF_EXTENSIONS = {".pdf"}
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff"}


async def convert_to_pdf(input_path: str, job_id: str, timeout: int = 120) -> str:
    """
    Convert a document to PDF.

    If the input is already a PDF, copies it to the job's pdf directory.
    If the input is an image, wraps it in a PDF.
    Otherwise, uses LibreOffice headless to convert.

    Returns the path to the resulting PDF.
    """
    ext = Path(input_path).suffix.lower()
    output_dir = get_job_dir(job_id) / "pdf"
    os.makedirs(output_dir, exist_ok=True)

    if ext in _PDF_EXTENSIONS:
        dest = output_dir / "reference.pdf"
        shutil.copy2(input_path, dest)
        logger.info(f"Job {job_id}: input is PDF, copied to {dest}")
        return str(dest)

    if ext in _IMAGE_EXTENSIONS:
        dest = output_dir / "reference.pdf"
        img = await asyncio.to_thread(Image.open, input_path)
        if img.mode == "RGBA":
            img = img.convert("RGB")
        await asyncio.to_thread(img.save, str(dest), "PDF")
        logger.info(f"Job {job_id}: converted image to PDF at {dest}")
        return str(dest)

    # LibreOffice headless conversion
    logger.info(f"Job {job_id}: converting {ext} to PDF via LibreOffice")
    try:
        dest = await _run_libreoffice_convert(input_path, output_dir, timeout)
    except (RuntimeError, TimeoutError) as exc:
        if not settings.ENABLE_REPAIR_ON_INGEST:
            raise
        logger.warning(f"Job {job_id}: normal conversion failed ({exc}), retrying with repair mode")
        dest = await _run_libreoffice_convert_with_repair(
            input_path,
            ext,
            output_dir,
            timeout,
        )

    logger.info(f"Job {job_id}: LibreOffice conversion complete → {dest}")
    return str(dest)


_LO_REPAIR_FILTERS: dict[str, str] = {
    ".docx": "Microsoft Word 2007-2019 XML",
    ".xlsx": "Calc MS Excel 2007 XML",
    ".pptx": "Impress MS PowerPoint 2007 XML",
    ".odt": "writer8",
    ".ods": "calc8",
    ".odp": "impress8",
}


async def _run_libreoffice_convert(
    input_path: str,
    output_dir: Path,
    timeout: int,
) -> str:
    """Run a standard LibreOffice headless conversion to PDF."""
    proc = await asyncio.create_subprocess_exec(
        "libreoffice",
        "--headless",
        "--norestore",
        "--convert-to",
        "pdf",
        "--outdir",
        str(output_dir),
        input_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise TimeoutError(f"LibreOffice conversion timed out after {timeout}s")

    if proc.returncode != 0:
        err = stderr.decode().strip()
        raise RuntimeError(f"LibreOffice conversion failed (exit {proc.returncode}): {err}")

    return _finalize_lo_output(input_path, output_dir)


async def _run_libreoffice_convert_with_repair(
    input_path: str,
    ext: str,
    output_dir: Path,
    timeout: int,
) -> str:
    """Run LibreOffice conversion with infilter repair mode enabled."""
    filter_name = _LO_REPAIR_FILTERS.get(ext)
    if not filter_name:
        raise RuntimeError(f"No repair filter available for {ext}")

    infilter = f"{filter_name}:repairmode"
    logger.info(f"Using LibreOffice repair filter: {infilter}")

    proc = await asyncio.create_subprocess_exec(
        "libreoffice",
        "--headless",
        "--norestore",
        f"--infilter={infilter}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(output_dir),
        input_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise TimeoutError(f"LibreOffice repair conversion timed out after {timeout}s")

    if proc.returncode != 0:
        err = stderr.decode().strip()
        raise RuntimeError(f"LibreOffice repair conversion failed (exit {proc.returncode}): {err}")

    return _finalize_lo_output(input_path, output_dir)


def _finalize_lo_output(input_path: str, output_dir: Path) -> str:
    """Locate the LibreOffice output PDF, rename to reference.pdf, and return the path."""
    stem = Path(input_path).stem
    pdf_path = output_dir / f"{stem}.pdf"
    if not pdf_path.exists():
        raise FileNotFoundError(f"Expected LibreOffice output at {pdf_path}, but file not found")

    dest = output_dir / "reference.pdf"
    pdf_path.rename(dest)
    return str(dest)


async def render_pages(pdf_path: str, job_id: str, dpi: int = 200) -> list[str]:
    """
    Render each page of a PDF to a PNG image.

    Returns a list of paths to the rendered page images.
    """
    logger.info(f"Job {job_id}: rendering pages from {pdf_path} at {dpi} DPI")

    # pdf2image is blocking (calls poppler subprocess), run in thread pool
    images: list[Image.Image] = await asyncio.to_thread(
        convert_from_path,
        pdf_path,
        dpi=dpi,
        fmt="png",
    )

    page_paths: list[str] = []
    for i, img in enumerate(images, start=1):
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        path = await save_rendered_page(job_id, i, buf.getvalue())
        page_paths.append(path)

    logger.info(f"Job {job_id}: rendered {len(page_paths)} pages")
    return page_paths


async def get_pdf_metadata(pdf_path: str) -> dict:
    """
    Extract basic metadata from a PDF using pikepdf.

    Returns dict with page_count, page_sizes, and orientations.
    """

    def _extract(path: str) -> dict:
        with pikepdf.open(path) as pdf:
            pages_info = []
            for page in pdf.pages:
                box = page.trimbox or page.mediabox
                width = float(box[2]) - float(box[0])
                height = float(box[3]) - float(box[1])
                # Convert from PDF points (72 dpi) to mm
                width_mm = round(width * 25.4 / 72, 1)
                height_mm = round(height * 25.4 / 72, 1)
                orientation = "landscape" if width > height else "portrait"
                pages_info.append(
                    {
                        "width_mm": width_mm,
                        "height_mm": height_mm,
                        "orientation": orientation,
                    }
                )
            return {
                "page_count": len(pdf.pages),
                "pages": pages_info,
            }

    return await asyncio.to_thread(_extract, pdf_path)
