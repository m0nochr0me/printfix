"""Page layout fixes: margins, page size, orientation, blank page removal."""

from __future__ import annotations

import asyncio
from xml.etree import ElementTree

from app.core.log import logger
from app.schema.fix import FixResult

__all__ = ("remove_blank_pages", "set_margins", "set_orientation", "set_page_size")

_WP_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


async def set_margins(
    file_path: str,
    job_id: str,
    top: float = 0.5,
    bottom: float = 0.5,
    left: float = 1.0,
    right: float = 0.75,
) -> FixResult:
    """Set margins on all sections of a DOCX. Values in inches."""
    return await asyncio.to_thread(
        _set_margins_sync, file_path, job_id, top, bottom, left, right,
    )


def _set_margins_sync(
    file_path: str, job_id: str,
    top: float, bottom: float, left: float, right: float,
) -> FixResult:
    from docx import Document
    from docx.shared import Inches

    doc = Document(file_path)
    changed_sections = 0
    old_values: list[str] = []

    for i, section in enumerate(doc.sections, 1):
        old = (
            f"S{i}: T={_emu_to_in(section.top_margin)} "
            f"B={_emu_to_in(section.bottom_margin)} "
            f"L={_emu_to_in(section.left_margin)} "
            f"R={_emu_to_in(section.right_margin)}"
        )
        old_values.append(old)

        section.top_margin = Inches(top)
        section.bottom_margin = Inches(bottom)
        section.left_margin = Inches(left)
        section.right_margin = Inches(right)
        changed_sections += 1

    doc.save(file_path)
    return FixResult(
        tool_name="set_margins",
        job_id=job_id,
        success=True,
        description=f"Set margins to T={top}\" B={bottom}\" L={left}\" R={right}\" on {changed_sections} section(s)",
        before_value="; ".join(old_values),
        after_value=f"T={top}\" B={bottom}\" L={left}\" R={right}\"",
    )


async def set_page_size(
    file_path: str,
    job_id: str,
    width: float = 8.5,
    height: float = 11.0,
) -> FixResult:
    """Set page size on all sections. Values in inches. A4=8.27x11.69, Letter=8.5x11."""
    return await asyncio.to_thread(_set_page_size_sync, file_path, job_id, width, height)


def _set_page_size_sync(
    file_path: str, job_id: str, width: float, height: float,
) -> FixResult:
    from docx import Document
    from docx.shared import Inches

    doc = Document(file_path)
    changed = 0

    for section in doc.sections:
        section.page_width = Inches(width)
        section.page_height = Inches(height)
        changed += 1

    doc.save(file_path)
    return FixResult(
        tool_name="set_page_size",
        job_id=job_id,
        success=True,
        description=f"Set page size to {width}\"x{height}\" on {changed} section(s)",
        after_value=f"{width}\"x{height}\"",
    )


async def set_orientation(
    file_path: str,
    job_id: str,
    orientation: str = "portrait",
) -> FixResult:
    """Set orientation on all sections. Swaps width/height as needed."""
    return await asyncio.to_thread(_set_orientation_sync, file_path, job_id, orientation)


def _set_orientation_sync(
    file_path: str, job_id: str, orientation: str,
) -> FixResult:
    from docx import Document
    from docx.enum.section import WD_ORIENT

    doc = Document(file_path)
    target = WD_ORIENT.PORTRAIT if orientation == "portrait" else WD_ORIENT.LANDSCAPE
    changed = 0

    for section in doc.sections:
        current = section.orientation
        if current != target:
            # Swap dimensions
            w, h = section.page_width, section.page_height
            section.page_width = h
            section.page_height = w
            section.orientation = target
            changed += 1

    doc.save(file_path)
    return FixResult(
        tool_name="set_orientation",
        job_id=job_id,
        success=True,
        description=f"Set orientation to {orientation} ({changed} section(s) changed)",
        after_value=orientation,
    )


async def remove_blank_pages(file_path: str, job_id: str) -> FixResult:
    """Remove consecutive page breaks that create blank pages in DOCX."""
    return await asyncio.to_thread(_remove_blank_pages_sync, file_path, job_id)


def _remove_blank_pages_sync(file_path: str, job_id: str) -> FixResult:
    from docx import Document

    doc = Document(file_path)
    removed = 0
    prev_was_break = False

    for para in list(doc.paragraphs):
        is_break_only = _is_page_break_paragraph(para)

        if is_break_only and prev_was_break:
            # Remove this paragraph (it creates a blank page)
            parent = para._element.getparent()
            if parent is not None:
                parent.remove(para._element)
                removed += 1
                continue

        prev_was_break = is_break_only

    doc.save(file_path)
    return FixResult(
        tool_name="remove_blank_pages",
        job_id=job_id,
        success=True,
        description=f"Removed {removed} consecutive page break(s) creating blank pages",
        after_value=f"{removed} removed",
    )


def _is_page_break_paragraph(para) -> bool:
    """Check if a paragraph is only a page break with no real content."""
    text = para.text.strip()
    if text:
        return False

    has_break = False
    for run in para.runs:
        xml = run._element.xml
        if 'w:type="page"' in xml or "w:type='page'" in xml:
            has_break = True

    if not has_break and para.paragraph_format.page_break_before:
        has_break = True

    return has_break


def _emu_to_in(emu) -> str:
    """Convert EMU to inches string for display."""
    if emu is None:
        return "?"
    return f"{emu / 914400:.2f}\""
