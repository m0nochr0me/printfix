"""
Printfix MCP server — document fix tools.
"""

from fastmcp import FastMCP

from app.core.log import logger
from app.fixes.common import record_fix, re_render_job, resolve_document
from app.fixes.page_layout import (
    remove_blank_pages as _remove_blank_pages,
    set_margins as _set_margins,
    set_orientation as _set_orientation,
    set_page_size as _set_page_size,
)
from app.fixes.typography import (
    adjust_font_size as _adjust_font_size,
    replace_font as _replace_font,
)
from app.fixes.tables import (
    auto_fit_tables as _auto_fit_tables,
    resize_table_text as _resize_table_text,
)
from app.fixes.page_breaks import (
    fix_page_breaks as _fix_page_breaks,
    remove_manual_breaks as _remove_manual_breaks,
)
from app.fixes.pdf_fallback import (
    pdf_crop_margins as _pdf_crop_margins,
    pdf_rotate_pages as _pdf_rotate_pages,
    pdf_scale_content as _pdf_scale_content,
)

__all__ = ("server",)

server = FastMCP("Printfix")


# -- Page & Layout --


@server.tool()
async def set_margins(
    job_id: str,
    top: float = 1.0,
    bottom: float = 1.0,
    left: float = 1.0,
    right: float = 1.0,
) -> str:
    """Set margins on all sections of a document. Values in inches."""
    file_path, _ = await resolve_document(job_id)
    result = await _set_margins(file_path, job_id, top, bottom, left, right)
    if result.success:
        await re_render_job(job_id)
    await record_fix(job_id, result)
    return result.model_dump_json()


@server.tool()
async def set_page_size(
    job_id: str,
    width: float = 8.5,
    height: float = 11.0,
) -> str:
    """Set page size on all sections. Values in inches. A4=8.27x11.69, Letter=8.5x11."""
    file_path, _ = await resolve_document(job_id)
    result = await _set_page_size(file_path, job_id, width, height)
    if result.success:
        await re_render_job(job_id)
    await record_fix(job_id, result)
    return result.model_dump_json()


@server.tool()
async def set_orientation(
    job_id: str,
    orientation: str = "portrait",
) -> str:
    """Set page orientation. Must be 'portrait' or 'landscape'."""
    file_path, _ = await resolve_document(job_id)
    result = await _set_orientation(file_path, job_id, orientation)
    if result.success:
        await re_render_job(job_id)
    await record_fix(job_id, result)
    return result.model_dump_json()


@server.tool()
async def remove_blank_pages(job_id: str) -> str:
    """Remove consecutive page breaks that create blank pages."""
    file_path, _ = await resolve_document(job_id)
    result = await _remove_blank_pages(file_path, job_id)
    if result.success:
        await re_render_job(job_id)
    await record_fix(job_id, result)
    return result.model_dump_json()


# -- Text & Typography --


@server.tool()
async def replace_font(
    job_id: str,
    from_font: str,
    to_font: str,
) -> str:
    """Replace all occurrences of a font with another."""
    file_path, _ = await resolve_document(job_id)
    result = await _replace_font(file_path, job_id, from_font, to_font)
    if result.success:
        await re_render_job(job_id)
    await record_fix(job_id, result)
    return result.model_dump_json()


@server.tool()
async def adjust_font_size(
    job_id: str,
    min_size_pt: float | None = None,
    max_size_pt: float | None = None,
) -> str:
    """Clamp all font sizes to a min/max range in points."""
    file_path, _ = await resolve_document(job_id)
    result = await _adjust_font_size(file_path, job_id, min_size_pt, max_size_pt)
    if result.success:
        await re_render_job(job_id)
    await record_fix(job_id, result)
    return result.model_dump_json()


# -- Tables --


@server.tool()
async def auto_fit_tables(job_id: str) -> str:
    """Auto-fit all tables to page width."""
    file_path, _ = await resolve_document(job_id)
    result = await _auto_fit_tables(file_path, job_id)
    if result.success:
        await re_render_job(job_id)
    await record_fix(job_id, result)
    return result.model_dump_json()


@server.tool()
async def resize_table_text(
    job_id: str,
    table_index: int = 0,
    max_font_size_pt: float = 10.0,
) -> str:
    """Reduce font size in a specific table's cells. Table index is 0-based."""
    file_path, _ = await resolve_document(job_id)
    result = await _resize_table_text(file_path, job_id, table_index, max_font_size_pt)
    if result.success:
        await re_render_job(job_id)
    await record_fix(job_id, result)
    return result.model_dump_json()


# -- Page Breaks --


@server.tool()
async def fix_page_breaks(
    job_id: str,
    strategy: str = "remove_consecutive",
) -> str:
    """Fix problematic page breaks. Strategy: 'remove_consecutive' or 'remove_all'."""
    file_path, _ = await resolve_document(job_id)
    result = await _fix_page_breaks(file_path, job_id, strategy)
    if result.success:
        await re_render_job(job_id)
    await record_fix(job_id, result)
    return result.model_dump_json()


@server.tool()
async def remove_manual_breaks(job_id: str) -> str:
    """Remove all manual page breaks from a document."""
    file_path, _ = await resolve_document(job_id)
    result = await _remove_manual_breaks(file_path, job_id)
    if result.success:
        await re_render_job(job_id)
    await record_fix(job_id, result)
    return result.model_dump_json()


# -- PDF Fallbacks --


@server.tool()
async def pdf_crop_margins(
    job_id: str,
    top: float = 0.5,
    bottom: float = 0.5,
    left: float = 0.5,
    right: float = 0.5,
) -> str:
    """Adjust PDF CropBox margins. Values in inches inset from edges."""
    pdf_path = await _get_pdf_path(job_id)
    result = await _pdf_crop_margins(pdf_path, job_id, top, bottom, left, right)
    if result.success:
        await re_render_job(job_id)
    await record_fix(job_id, result)
    return result.model_dump_json()


@server.tool()
async def pdf_scale_content(
    job_id: str,
    scale_factor: float = 0.9,
) -> str:
    """Scale all PDF page content by a factor (e.g. 0.9 = shrink to 90%)."""
    pdf_path = await _get_pdf_path(job_id)
    result = await _pdf_scale_content(pdf_path, job_id, scale_factor)
    if result.success:
        await re_render_job(job_id)
    await record_fix(job_id, result)
    return result.model_dump_json()


@server.tool()
async def pdf_rotate_pages(
    job_id: str,
    pages: list[int] | None = None,
    angle: int = 90,
) -> str:
    """Rotate PDF pages. Angle must be 0/90/180/270. Pages are 1-indexed; None=all."""
    pdf_path = await _get_pdf_path(job_id)
    result = await _pdf_rotate_pages(pdf_path, job_id, pages, angle)
    if result.success:
        await re_render_job(job_id)
    await record_fix(job_id, result)
    return result.model_dump_json()


# -- Helpers --


async def _get_pdf_path(job_id: str) -> str:
    """Get the reference PDF path for a job."""
    from app.worker.job_state import JobStateManager

    job = await JobStateManager.get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")
    pdf_path = job.get("pdf_path", "")
    if not pdf_path:
        raise FileNotFoundError(f"No PDF found for job {job_id}")
    return pdf_path
