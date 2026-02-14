"""PDF-specific fallback fixes using pikepdf."""

from __future__ import annotations

import asyncio

import pikepdf

from app.core.log import logger
from app.schema.fix import FixResult

__all__ = ("pdf_crop_margins", "pdf_embed_fonts", "pdf_normalize_page_sizes", "pdf_rotate_pages", "pdf_scale_content")

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
                for stream in contents:  # type: ignore
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


# ── Page size normalization ──────────────────────────────────────────────


async def pdf_normalize_page_sizes(
    pdf_path: str,
    job_id: str,
    target_width: float = 8.27,
    target_height: float = 11.69,
) -> FixResult:
    """Normalize all pages to the same size, scaling content proportionally.

    Target dimensions in inches. Default is A4 (8.27x11.69).
    Pages that already match the target size are left unchanged.
    """
    return await asyncio.to_thread(
        _pdf_normalize_page_sizes_sync,
        pdf_path,
        job_id,
        target_width,
        target_height,
    )


def _pdf_normalize_page_sizes_sync(
    pdf_path: str,
    job_id: str,
    target_width: float,
    target_height: float,
) -> FixResult:
    target_w_pt = target_width * _PT_PER_INCH
    target_h_pt = target_height * _PT_PER_INCH
    tolerance = 1.0  # 1pt tolerance for "same size" check

    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        pages_affected = []

        for i, page in enumerate(pdf.pages, 1):
            mb = page.mediabox
            cur_w = float(mb[2]) - float(mb[0])
            cur_h = float(mb[3]) - float(mb[1])

            # Skip pages that already match target
            if abs(cur_w - target_w_pt) < tolerance and abs(cur_h - target_h_pt) < tolerance:
                continue

            # Calculate scale to fit content within new page
            scale_x = target_w_pt / cur_w if cur_w > 0 else 1.0
            scale_y = target_h_pt / cur_h if cur_h > 0 else 1.0
            scale = min(scale_x, scale_y)  # Uniform scale, preserving aspect ratio

            # Center the scaled content on the new page
            dx = (target_w_pt - cur_w * scale) / 2
            dy = (target_h_pt - cur_h * scale) / 2

            # Wrap existing content with transform
            contents = page.get("/Contents")
            if contents is not None:
                if isinstance(contents, pikepdf.Array):
                    old_data = b""
                    for stream in contents:
                        old_data += pikepdf.Stream(pdf, stream).read_bytes()
                else:
                    old_data = contents.read_bytes()

                transform = f"{scale} 0 0 {scale} {dx} {dy} cm\n"
                new_data = b"q\n" + transform.encode() + old_data + b"\nQ\n"
                page["/Contents"] = pdf.make_stream(new_data)

            # Update MediaBox to target size
            page["/MediaBox"] = pikepdf.Array([0, 0, target_w_pt, target_h_pt])
            # Remove CropBox if present (will inherit from MediaBox)
            if "/CropBox" in page:
                del page["/CropBox"]

            pages_affected.append(i)

        pdf.save(pdf_path)

    return FixResult(
        tool_name="pdf_normalize_page_sizes",
        job_id=job_id,
        success=True,
        description=(f'Normalized {len(pages_affected)} page(s) to {target_width}"x{target_height}"'),
        pages_affected=pages_affected,
        after_value=f'{target_width}"x{target_height}"',
    )


# ── Font embedding ───────────────────────────────────────────────────────


async def pdf_embed_fonts(
    pdf_path: str,
    job_id: str,
) -> FixResult:
    """Attempt to embed non-embedded fonts in a PDF.

    Uses pikepdf to identify non-embedded fonts and attempts to embed them
    by resolving system fonts. This is a best-effort operation — fonts that
    cannot be found on the system will be skipped.
    """
    return await asyncio.to_thread(_pdf_embed_fonts_sync, pdf_path, job_id)


def _pdf_embed_fonts_sync(pdf_path: str, job_id: str) -> FixResult:
    import subprocess
    import tempfile

    # Use Ghostscript for font embedding as it's more reliable than
    # manual pikepdf font manipulation
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name

        result = subprocess.run(
            [
                "gs",
                "-dNOPAUSE",
                "-dBATCH",
                "-sDEVICE=pdfwrite",
                "-dPDFSETTINGS=/prepress",
                "-dEmbedAllFonts=true",
                "-dSubsetFonts=true",
                "-dCompressFonts=true",
                f"-sOutputFile={tmp_path}",
                pdf_path,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            logger.warning(f"Job {job_id}: gs font embedding returned {result.returncode}: {result.stderr[:500]}")
            return FixResult(
                tool_name="pdf_embed_fonts",
                job_id=job_id,
                success=False,
                description=f"Ghostscript font embedding failed: {result.stderr[:200]}",
                error=result.stderr[:500],
            )

        # Replace original with processed file
        import shutil

        shutil.move(tmp_path, pdf_path)

        return FixResult(
            tool_name="pdf_embed_fonts",
            job_id=job_id,
            success=True,
            description="Embedded fonts via Ghostscript (prepress settings)",
            after_value="fonts embedded",
        )

    except FileNotFoundError:
        return FixResult(
            tool_name="pdf_embed_fonts",
            job_id=job_id,
            success=False,
            description="Ghostscript (gs) not found — required for font embedding",
            error="gs not installed",
        )
    except subprocess.TimeoutExpired:
        return FixResult(
            tool_name="pdf_embed_fonts",
            job_id=job_id,
            success=False,
            description="Font embedding timed out after 120s",
            error="timeout",
        )
    except Exception as exc:
        logger.error(f"Job {job_id}: pdf_embed_fonts failed: {exc}")
        return FixResult(
            tool_name="pdf_embed_fonts",
            job_id=job_id,
            success=False,
            description=f"Failed to embed fonts: {exc}",
            error=str(exc),
        )
