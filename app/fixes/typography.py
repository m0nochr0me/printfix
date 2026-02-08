"""Font fixes: replacement and size adjustment."""

from __future__ import annotations

import asyncio

from app.core.log import logger
from app.schema.fix import FixResult

__all__ = ("adjust_font_size", "replace_font")


async def replace_font(
    file_path: str,
    job_id: str,
    from_font: str,
    to_font: str,
) -> FixResult:
    """Replace all occurrences of a font with another in a DOCX."""
    return await asyncio.to_thread(
        _replace_font_sync, file_path, job_id, from_font, to_font,
    )


def _replace_font_sync(
    file_path: str, job_id: str, from_font: str, to_font: str,
) -> FixResult:
    from docx import Document

    doc = Document(file_path)
    replaced = 0

    # Walk paragraphs
    for para in doc.paragraphs:
        for run in para.runs:
            if run.font.name == from_font:
                run.font.name = to_font
                replaced += 1

    # Walk table cells
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    for run in para.runs:
                        if run.font.name == from_font:
                            run.font.name = to_font
                            replaced += 1

    doc.save(file_path)
    return FixResult(
        tool_name="replace_font",
        job_id=job_id,
        success=True,
        description=f"Replaced '{from_font}' with '{to_font}' in {replaced} run(s)",
        before_value=from_font,
        after_value=to_font,
    )


async def adjust_font_size(
    file_path: str,
    job_id: str,
    min_size_pt: float | None = None,
    max_size_pt: float | None = None,
) -> FixResult:
    """Clamp all font sizes to a min/max range in a DOCX."""
    return await asyncio.to_thread(
        _adjust_font_size_sync, file_path, job_id, min_size_pt, max_size_pt,
    )


def _adjust_font_size_sync(
    file_path: str, job_id: str,
    min_size_pt: float | None, max_size_pt: float | None,
) -> FixResult:
    from docx import Document
    from docx.shared import Pt

    doc = Document(file_path)
    adjusted = 0

    def clamp_run(run) -> bool:
        nonlocal adjusted
        if run.font.size is None:
            return False

        pt = run.font.size / 12700  # EMU to pt
        new_pt = pt

        if min_size_pt is not None and pt < min_size_pt:
            new_pt = min_size_pt
        if max_size_pt is not None and pt > max_size_pt:
            new_pt = max_size_pt

        if new_pt != pt:
            run.font.size = Pt(new_pt)
            adjusted += 1
            return True
        return False

    for para in doc.paragraphs:
        for run in para.runs:
            clamp_run(run)

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    for run in para.runs:
                        clamp_run(run)

    doc.save(file_path)

    range_desc = ""
    if min_size_pt is not None and max_size_pt is not None:
        range_desc = f"{min_size_pt}-{max_size_pt}pt"
    elif min_size_pt is not None:
        range_desc = f">={min_size_pt}pt"
    elif max_size_pt is not None:
        range_desc = f"<={max_size_pt}pt"

    return FixResult(
        tool_name="adjust_font_size",
        job_id=job_id,
        success=True,
        description=f"Adjusted {adjusted} run(s) to {range_desc}",
        after_value=range_desc,
    )
