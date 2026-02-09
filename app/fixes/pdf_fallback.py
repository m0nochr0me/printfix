"""PDF-specific fallback fixes using pikepdf."""

from __future__ import annotations

import asyncio

import pikepdf

from app.core.log import logger
from app.schema.fix import FixResult

__all__ = ("pdf_crop_margins", "pdf_rotate_pages", "pdf_scale_content")

_PT_PER_INCH = 72.0


async def pdf_crop_margins(
    pdf_path: str,
    job_id: str,
    top: float = 0.5,
    bottom: float = 0.5,
    left: float = 0.5,
    right: float = 0.5,
) -> FixResult:
    """Adjust CropBox on all pages. Values in inches inset from MediaBox edges."""
    return await asyncio.to_thread(
        _pdf_crop_margins_sync,
        pdf_path,
        job_id,
        top,
        bottom,
        left,
        right,
    )


def _pdf_crop_margins_sync(
    pdf_path: str,
    job_id: str,
    top: float,
    bottom: float,
    left: float,
    right: float,
) -> FixResult:
    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        pages_affected = []
        for i, page in enumerate(pdf.pages, 1):
            mb = page.mediabox
            x0 = float(mb[0]) + left * _PT_PER_INCH
            y0 = float(mb[1]) + bottom * _PT_PER_INCH
            x1 = float(mb[2]) - right * _PT_PER_INCH
            y1 = float(mb[3]) - top * _PT_PER_INCH

            if x1 > x0 and y1 > y0:
                page["/CropBox"] = pikepdf.Array([x0, y0, x1, y1])
                pages_affected.append(i)

        pdf.save(pdf_path)

    return FixResult(
        tool_name="pdf_crop_margins",
        job_id=job_id,
        success=True,
        description=f'Set CropBox margins (T={top}" B={bottom}" L={left}" R={right}") on {len(pages_affected)} page(s)',
        pages_affected=pages_affected,
        after_value=f'T={top}" B={bottom}" L={left}" R={right}"',
    )


async def pdf_scale_content(
    pdf_path: str,
    job_id: str,
    scale_factor: float = 0.9,
) -> FixResult:
    """Scale all page content by a factor (e.g. 0.9 = shrink to 90%)."""
    return await asyncio.to_thread(
        _pdf_scale_content_sync,
        pdf_path,
        job_id,
        scale_factor,
    )


def _pdf_scale_content_sync(
    pdf_path: str,
    job_id: str,
    scale_factor: float,
) -> FixResult:
    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        pages_affected = []
        for i, page in enumerate(pdf.pages, 1):
            mb = page.mediabox
            w = float(mb[2]) - float(mb[0])
            h = float(mb[3]) - float(mb[1])

            # Calculate offset to center the scaled content
            dx = w * (1 - scale_factor) / 2
            dy = h * (1 - scale_factor) / 2

            # Create a transform matrix: scale + translate
            transform = f"{scale_factor} 0 0 {scale_factor} {dx} {dy} cm\n"

            # Wrap existing contents stream
            contents = page.get("/Contents")
            if contents is None:
                continue

            # Read existing content
            if isinstance(contents, pikepdf.Array):
                old_data = b""
                for stream in contents: # type: ignore
                    old_data += pikepdf.Stream(pdf, stream).read_bytes()
            else:
                old_data = contents.read_bytes()

            # Prepend the transform
            new_data = b"q\n" + transform.encode() + old_data + b"\nQ\n"
            page["/Contents"] = pdf.make_stream(new_data)
            pages_affected.append(i)

        pdf.save(pdf_path)

    return FixResult(
        tool_name="pdf_scale_content",
        job_id=job_id,
        success=True,
        description=f"Scaled content to {scale_factor * 100:.0f}% on {len(pages_affected)} page(s)",
        pages_affected=pages_affected,
        after_value=f"{scale_factor * 100:.0f}%",
    )


async def pdf_rotate_pages(
    pdf_path: str,
    job_id: str,
    pages: list[int] | None = None,
    angle: int = 90,
) -> FixResult:
    """Rotate specific pages. Angle must be 0/90/180/270. Pages are 1-indexed."""
    if angle not in (0, 90, 180, 270):
        return FixResult(
            tool_name="pdf_rotate_pages",
            job_id=job_id,
            success=False,
            description=f"Invalid angle: {angle}. Must be 0, 90, 180, or 270.",
            error="Invalid angle",
        )
    return await asyncio.to_thread(
        _pdf_rotate_pages_sync,
        pdf_path,
        job_id,
        pages,
        angle,
    )


def _pdf_rotate_pages_sync(
    pdf_path: str,
    job_id: str,
    pages: list[int] | None,
    angle: int,
) -> FixResult:
    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        pages_affected = []
        target_pages = pages or list(range(1, len(pdf.pages) + 1))

        for page_num in target_pages:
            if 1 <= page_num <= len(pdf.pages):
                page = pdf.pages[page_num - 1]
                current = int(page.get("/Rotate", 0))
                page["/Rotate"] = (current + angle) % 360
                pages_affected.append(page_num)

        pdf.save(pdf_path)

    return FixResult(
        tool_name="pdf_rotate_pages",
        job_id=job_id,
        success=True,
        description=f"Rotated {len(pages_affected)} page(s) by {angle}°",
        pages_affected=pages_affected,
        after_value=f"{angle}°",
    )
