"""
Printfix MCP server — document fix tools.
"""

from typing import Annotated

from fastmcp import FastMCP

from app.core.log import logger
from app.fixes.common import re_render_job, record_fix, resolve_document
from app.fixes.page_breaks import (
    fix_page_breaks as _fix_page_breaks,
)
from app.fixes.page_breaks import (
    remove_manual_breaks as _remove_manual_breaks,
)
from app.fixes.page_layout import (
    adjust_paragraph_indents as _adjust_paragraph_indents,
)
from app.fixes.page_layout import (
    remove_blank_pages as _remove_blank_pages,
)
from app.fixes.page_layout import (
    set_margins as _set_margins,
)
from app.fixes.page_layout import (
    set_orientation as _set_orientation,
)
from app.fixes.page_layout import (
    set_page_size as _set_page_size,
)
from app.fixes.images import (
    check_image_dpi as _check_image_dpi,
)
from app.fixes.images import (
    convert_pdf_colorspace as _convert_pdf_colorspace,
)
from app.fixes.images import (
    resize_images_to_fit as _resize_images_to_fit,
)
from app.fixes.pptx import (
    adjust_pptx_font_size as _adjust_pptx_font_size,
)
from app.fixes.pptx import (
    set_pptx_slide_size as _set_pptx_slide_size,
)
from app.fixes.xlsx import (
    set_xlsx_margins as _set_xlsx_margins,
)
from app.fixes.xlsx import (
    set_xlsx_page_setup as _set_xlsx_page_setup,
)
from app.fixes.xlsx import (
    auto_fit_xlsx_columns as _auto_fit_xlsx_columns,
)
from app.fixes.pdf_fallback import (
    pdf_crop_margins as _pdf_crop_margins,
)
from app.fixes.pdf_fallback import (
    pdf_rotate_pages as _pdf_rotate_pages,
)
from app.fixes.pdf_fallback import (
    pdf_scale_content as _pdf_scale_content,
)
from app.fixes.tables import (
    auto_fit_tables as _auto_fit_tables,
)
from app.fixes.tables import (
    resize_table_text as _resize_table_text,
)
from app.fixes.typography import (
    adjust_font_size as _adjust_font_size,
)
from app.fixes.typography import (
    replace_font as _replace_font,
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


@server.tool()
async def adjust_paragraph_indents(
    job_id: str,
    max_left_inches: float = 0.5,
    max_right_inches: float = 0.5,
    max_first_line_inches: float = 0.5,
    strategy: str = "cap",
) -> str:
    """Adjust paragraph indents in a DOCX to reclaim printable space.

    Reduces excessive left/right/first-line indents that waste page area,
    especially after margins have been tightened.
    Strategy: 'cap' (clamp at max) or 'scale' (proportionally shrink).
    Values in inches.
    """
    file_path, _ = await resolve_document(job_id)
    result = await _adjust_paragraph_indents(
        file_path, job_id,
        max_left_inches, max_right_inches, max_first_line_inches,
        strategy,
    )
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


# -- Image Tools --


@server.tool()
async def convert_colorspace(
    job_id: str,
    target_colorspace: str = "cmyk",
) -> str:
    """Convert RGB images in a PDF to CMYK for professional print output."""
    pdf_path = await _get_pdf_path(job_id)
    result = await _convert_pdf_colorspace(pdf_path, job_id, target_colorspace)
    if result.success:
        await re_render_job(job_id)
    await record_fix(job_id, result)
    return result.model_dump_json()


@server.tool()
async def check_image_dpi(
    job_id: str,
    min_dpi: int = 150,
) -> str:
    """Report low-DPI images in a PDF. Flags but cannot upscale."""
    pdf_path = await _get_pdf_path(job_id)
    result = await _check_image_dpi(pdf_path, job_id, min_dpi)
    await record_fix(job_id, result)
    return result.model_dump_json()


@server.tool()
async def resize_images_to_fit(
    job_id: str,
    max_width_pct: float = 100.0,
    max_height_pct: float = 90.0,
) -> str:
    """Proportionally resize images in a DOCX that exceed the printable area.

    Images are scaled down preserving aspect ratio so they fit within the
    printable page dimensions. max_width_pct/max_height_pct control the
    maximum percentage of printable area an image may occupy.
    """
    file_path, _ = await resolve_document(job_id)
    result = await _resize_images_to_fit(
        file_path, job_id, max_width_pct, max_height_pct,
    )
    if result.success:
        await re_render_job(job_id)
    await record_fix(job_id, result)
    return result.model_dump_json()


# -- XLSX Tools --


@server.tool()
async def set_xlsx_margins(
    job_id: str,
    top: float = 0.75,
    bottom: float = 0.75,
    left: float = 0.75,
    right: float = 0.75,
) -> str:
    """Set print margins on all sheets of an XLSX file. Values in inches."""
    file_path, _ = await resolve_document(job_id)
    result = await _set_xlsx_margins(file_path, job_id, top, bottom, left, right)
    if result.success:
        await re_render_job(job_id)
    await record_fix(job_id, result)
    return result.model_dump_json()


@server.tool()
async def set_xlsx_page_setup(
    job_id: str,
    orientation: str = "portrait",
    paper_size: int = 1,
    fit_to_page: bool = True,
) -> str:
    """Configure print page setup on all sheets. paper_size: 1=Letter, 9=A4."""
    file_path, _ = await resolve_document(job_id)
    result = await _set_xlsx_page_setup(
        file_path, job_id, orientation, paper_size, fit_to_page,
    )
    if result.success:
        await re_render_job(job_id)
    await record_fix(job_id, result)
    return result.model_dump_json()


@server.tool()
async def auto_fit_xlsx_columns(
    job_id: str,
    max_col_width: float = 30.0,
    min_col_width: float = 5.0,
    shrink_margins: bool = True,
) -> str:
    """Auto-fit XLSX columns to fit within a single page width.

    Analyses content widths, auto-selects portrait/landscape orientation,
    tightens margins, and enables fit-to-page. Ideal for sheets where
    columns overflow to additional printed pages.
    """
    file_path, _ = await resolve_document(job_id)
    result = await _auto_fit_xlsx_columns(
        file_path, job_id, max_col_width, min_col_width, shrink_margins,
    )
    if result.success:
        await re_render_job(job_id)
    await record_fix(job_id, result)
    return result.model_dump_json()


# -- PPTX Tools --


@server.tool()
async def set_pptx_slide_size(
    job_id: str,
    width: float = 10.0,
    height: float = 7.5,
) -> str:
    """Set slide dimensions. Values in inches. Default 4:3 standard (10x7.5)."""
    file_path, _ = await resolve_document(job_id)
    result = await _set_pptx_slide_size(file_path, job_id, width, height)
    if result.success:
        await re_render_job(job_id)
    await record_fix(job_id, result)
    return result.model_dump_json()


@server.tool()
async def adjust_pptx_font_size(
    job_id: str,
    min_size_pt: float = 10.0,
) -> str:
    """Enforce minimum font size across all text in a PPTX presentation."""
    file_path, _ = await resolve_document(job_id)
    result = await _adjust_pptx_font_size(file_path, job_id, min_size_pt)
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
