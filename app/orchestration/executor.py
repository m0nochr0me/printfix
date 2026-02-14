"""
Fix executor — maps tool names to fix functions and runs them.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any, Callable, Coroutine

from app.core.config import settings
from app.core.integrity import cleanup_backup, create_backup, validate_after_fix
from app.core.log import logger
from app.fixes.cleanup import accept_tracked_changes, normalize_styles, remove_empty_paragraphs, strip_hidden_text
from app.fixes.common import re_render_job, record_fix, resolve_document
from app.fixes.images import check_image_dpi, convert_pdf_colorspace, resize_images_to_fit
from app.fixes.page_breaks import fix_page_breaks, remove_manual_breaks
from app.fixes.page_layout import (
    adjust_paragraph_indents,
    remove_blank_pages,
    set_columns,
    set_margins,
    set_orientation,
    set_page_size,
)
from app.fixes.pdf_fallback import (
    pdf_crop_margins,
    pdf_embed_fonts,
    pdf_normalize_page_sizes,
    pdf_rotate_pages,
    pdf_scale_content,
)
from app.fixes.pptx import (
    adjust_pptx_font_size,
    replace_pptx_font,
    reposition_pptx_shapes,
    resize_pptx_text_boxes,
    set_pptx_slide_size,
)
from app.fixes.tables import auto_fit_tables, resize_table_text
from app.fixes.typography import (
    adjust_font_size,
    normalize_paragraph_spacing,
    replace_font,
    set_line_spacing,
    set_widow_orphan_control,
)
from app.fixes.xlsx import (
    adjust_xlsx_font_size,
    auto_fit_xlsx_columns,
    replace_xlsx_font,
    scale_xlsx_row_heights,
    set_xlsx_margins,
    set_xlsx_page_setup,
    set_xlsx_print_area,
)
from app.schema.fix import FixResult
from app.schema.orchestration import FixAction
from app.worker.job_state import JobStateManager

__all__ = ("execute_fix", "execute_plan")

# Type alias for fix functions
FixFunc = Callable[..., Coroutine[Any, Any, FixResult]]

# ── Tool registry ────────────────────────────────────────────────────────

# Maps tool_name → (fix_function, is_pdf_tool)
TOOL_REGISTRY: dict[str, tuple[FixFunc, bool]] = {
    # DOCX tools
    "adjust_paragraph_indents": (adjust_paragraph_indents, False),
    "set_margins": (set_margins, False),
    "set_page_size": (set_page_size, False),
    "set_columns": (set_columns, False),
    "set_orientation": (set_orientation, False),
    "remove_blank_pages": (remove_blank_pages, False),
    "replace_font": (replace_font, False),
    "adjust_font_size": (adjust_font_size, False),
    "auto_fit_tables": (auto_fit_tables, False),
    "resize_table_text": (resize_table_text, False),
    "fix_page_breaks": (fix_page_breaks, False),
    "remove_manual_breaks": (remove_manual_breaks, False),
    # DOCX cleanup
    "accept_tracked_changes": (accept_tracked_changes, False),
    "strip_hidden_text": (strip_hidden_text, False),
    "remove_empty_paragraphs": (remove_empty_paragraphs, False),
    "normalize_styles": (normalize_styles, False),
    # DOCX typography & spacing
    "set_widow_orphan_control": (set_widow_orphan_control, False),
    "normalize_paragraph_spacing": (normalize_paragraph_spacing, False),
    "set_line_spacing": (set_line_spacing, False),
    # PDF tools
    "pdf_crop_margins": (pdf_crop_margins, True),
    "pdf_scale_content": (pdf_scale_content, True),
    "pdf_rotate_pages": (pdf_rotate_pages, True),
    "pdf_normalize_page_sizes": (pdf_normalize_page_sizes, True),
    "pdf_embed_fonts": (pdf_embed_fonts, True),
    # Image tools (PDF-level)
    "convert_colorspace": (convert_pdf_colorspace, True),
    "check_image_dpi": (check_image_dpi, True),
    # XLSX tools
    "set_xlsx_margins": (set_xlsx_margins, False),
    "set_xlsx_page_setup": (set_xlsx_page_setup, False),
    "auto_fit_xlsx_columns": (auto_fit_xlsx_columns, False),
    "adjust_xlsx_font_size": (adjust_xlsx_font_size, False),
    "replace_xlsx_font": (replace_xlsx_font, False),
    "set_xlsx_print_area": (set_xlsx_print_area, False),
    "scale_xlsx_row_heights": (scale_xlsx_row_heights, False),
    # DOCX image tools
    "resize_images_to_fit": (resize_images_to_fit, False),
    # PPTX tools
    "set_pptx_slide_size": (set_pptx_slide_size, False),
    "adjust_pptx_font_size": (adjust_pptx_font_size, False),
    "reposition_pptx_shapes": (reposition_pptx_shapes, False),
    "replace_pptx_font": (replace_pptx_font, False),
    "resize_pptx_text_boxes": (resize_pptx_text_boxes, False),
}


async def execute_fix(job_id: str, action: FixAction) -> FixResult:
    """
    Execute a single FixAction.

    Resolves the document path, calls the appropriate fix function,
    re-renders on success, and records the result.
    """
    tool_name = action.tool_name

    if tool_name not in TOOL_REGISTRY:
        result = FixResult(
            tool_name=tool_name,
            job_id=job_id,
            success=False,
            description=f"Unknown tool: {tool_name}",
            error=f"Tool '{tool_name}' not found in registry",
        )
        await record_fix(job_id, result)
        return result

    fix_func, is_pdf = TOOL_REGISTRY[tool_name]
    backup_path: str | None = None

    try:
        if is_pdf:
            file_path = await _get_pdf_path(job_id)
            file_type_ext = ".pdf"
        else:
            file_path, file_type_ext = await resolve_document(job_id)

        # Create backup before applying the fix
        if settings.ENABLE_POST_FIX_VALIDATION:
            backup_path = await create_backup(file_path)

        result = await asyncio.wait_for(
            fix_func(file_path, job_id, **action.params),
            timeout=settings.FIX_EXECUTION_TIMEOUT_SECONDS,
        )

        if result.success:
            # Validate the output file wasn't corrupted by the fix
            if settings.ENABLE_POST_FIX_VALIDATION and backup_path:
                post_check = await validate_after_fix(file_path, file_type_ext, backup_path)
                backup_path = None  # handled by validate_after_fix
                if not post_check.valid:
                    logger.error(
                        f"Job {job_id}: {tool_name} corrupted the file, restored from backup — {post_check.details}"
                    )
                    result = FixResult(
                        tool_name=tool_name,
                        job_id=job_id,
                        success=False,
                        description=f"{tool_name} produced corrupt output, file restored from backup",
                        error=f"Post-fix validation failed: {post_check.details}",
                        timestamp=datetime.now(UTC),
                    )
                    await record_fix(job_id, result)
                    return result

            await re_render_job(job_id)

        await record_fix(job_id, result)

        logger.info(
            f"Job {job_id}: {tool_name} "
            f"{'succeeded' if result.success else 'failed'}"
            f"{': ' + result.description if result.description else ''}"
        )
        return result

    except asyncio.TimeoutError:
        result = FixResult(
            tool_name=tool_name,
            job_id=job_id,
            success=False,
            description=f"{tool_name} timed out after {settings.FIX_EXECUTION_TIMEOUT_SECONDS}s",
            error="timeout",
            timestamp=datetime.now(UTC),
        )
        await record_fix(job_id, result)
        logger.error(f"Job {job_id}: {tool_name} timed out")
        return result

    except Exception as exc:
        result = FixResult(
            tool_name=tool_name,
            job_id=job_id,
            success=False,
            description=f"Exception during {tool_name}: {exc}",
            error=str(exc),
            timestamp=datetime.now(UTC),
        )
        await record_fix(job_id, result)
        logger.error(f"Job {job_id}: {tool_name} raised {exc}")
        return result

    finally:
        # Clean up backup if it wasn't already handled
        if backup_path:
            await cleanup_backup(backup_path)


async def execute_plan(
    job_id: str,
    actions: list[FixAction],
) -> tuple[int, int, set[str], bool]:
    """
    Execute all actions in a FixPlan sequentially.

    Returns (applied_count, failed_count, failed_issue_types, used_fallback).
    ``failed_issue_types`` contains the ``target_issues`` of non-fallback
    actions that failed — used by the orchestrator to trigger PDF fallback
    in subsequent iterations.
    """
    applied = 0
    failed = 0
    failed_issue_types: set[str] = set()
    used_fallback = False
    for action in actions:
        result = await execute_fix(job_id, action)
        if result.success:
            applied += 1
            if action.is_fallback:
                used_fallback = True
        else:
            failed += 1
            if not action.is_fallback:
                failed_issue_types.update(action.target_issues)
    return applied, failed, failed_issue_types, used_fallback


async def _get_pdf_path(job_id: str) -> str:
    """Get the reference PDF path for a job."""

    job = await JobStateManager.get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")
    pdf_path = job.get("pdf_path", "")
    if not pdf_path:
        raise FileNotFoundError(f"No PDF found for job {job_id}")
    return pdf_path
