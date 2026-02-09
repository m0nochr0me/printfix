"""PPTX fix tools: slide size and font adjustments."""

from __future__ import annotations

import asyncio

from app.core.log import logger
from app.schema.fix import FixResult

__all__ = ("set_pptx_slide_size", "adjust_pptx_font_size")

_EMU_PER_INCH = 914400


async def set_pptx_slide_size(
    file_path: str,
    job_id: str,
    width: float = 10.0,
    height: float = 7.5,
) -> FixResult:
    """Set slide dimensions. Values in inches. Default is 4:3 standard (10x7.5)."""
    return await asyncio.to_thread(
        _set_pptx_slide_size_sync, file_path, job_id, width, height,
    )


def _set_pptx_slide_size_sync(
    file_path: str,
    job_id: str,
    width: float,
    height: float,
) -> FixResult:
    from pptx import Presentation

    try:
        prs = Presentation(file_path)

        old_w = prs.slide_width / _EMU_PER_INCH if prs.slide_width else 0
        old_h = prs.slide_height / _EMU_PER_INCH if prs.slide_height else 0

        prs.slide_width = int(width * _EMU_PER_INCH)
        prs.slide_height = int(height * _EMU_PER_INCH)

        prs.save(file_path)

        return FixResult(
            tool_name="set_pptx_slide_size",
            job_id=job_id,
            success=True,
            description=(
                f"Changed slide size from {old_w:.1f}\"x{old_h:.1f}\" "
                f"to {width:.1f}\"x{height:.1f}\""
            ),
            before_value=f"{old_w:.1f}x{old_h:.1f}",
            after_value=f"{width:.1f}x{height:.1f}",
        )
    except Exception as exc:
        logger.error(f"Job {job_id}: set_pptx_slide_size failed: {exc}")
        return FixResult(
            tool_name="set_pptx_slide_size",
            job_id=job_id,
            success=False,
            description=f"Failed to set PPTX slide size: {exc}",
            error=str(exc),
        )


async def adjust_pptx_font_size(
    file_path: str,
    job_id: str,
    min_size_pt: float = 10.0,
) -> FixResult:
    """Enforce a minimum font size across all text in the presentation."""
    return await asyncio.to_thread(
        _adjust_pptx_font_size_sync, file_path, job_id, min_size_pt,
    )


def _adjust_pptx_font_size_sync(
    file_path: str,
    job_id: str,
    min_size_pt: float,
) -> FixResult:
    from pptx import Presentation
    from pptx.util import Pt

    try:
        prs = Presentation(file_path)
        adjusted = 0
        min_emu = int(min_size_pt * 12700)

        for slide in prs.slides:
            for shape in slide.shapes:
                if not shape.has_text_frame:
                    continue
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        if run.font.size is not None and run.font.size < min_emu:
                            run.font.size = Pt(min_size_pt)
                            adjusted += 1

        prs.save(file_path)

        if adjusted == 0:
            return FixResult(
                tool_name="adjust_pptx_font_size",
                job_id=job_id,
                success=True,
                description=f"No text runs below {min_size_pt}pt found",
            )

        return FixResult(
            tool_name="adjust_pptx_font_size",
            job_id=job_id,
            success=True,
            description=f"Adjusted {adjusted} text run(s) to minimum {min_size_pt}pt",
            after_value=f"min {min_size_pt}pt",
        )
    except Exception as exc:
        logger.error(f"Job {job_id}: adjust_pptx_font_size failed: {exc}")
        return FixResult(
            tool_name="adjust_pptx_font_size",
            job_id=job_id,
            success=False,
            description=f"Failed to adjust PPTX font size: {exc}",
            error=str(exc),
        )
