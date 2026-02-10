"""Image fix tools: colorspace conversion and DPI reporting."""

from __future__ import annotations

import asyncio
import io

import pikepdf
from PIL import Image

from app.core.log import logger
from app.schema.fix import FixResult

__all__ = ("check_image_dpi", "convert_pdf_colorspace", "resize_images_to_fit")

_PT_PER_INCH = 72.0


async def convert_pdf_colorspace(
    pdf_path: str,
    job_id: str,
    target_colorspace: str = "cmyk",
) -> FixResult:
    """Convert RGB images in a PDF to CMYK for professional print."""
    return await asyncio.to_thread(
        _convert_pdf_colorspace_sync, pdf_path, job_id, target_colorspace,
    )


def _convert_pdf_colorspace_sync(
    pdf_path: str,
    job_id: str,
    target_colorspace: str,
) -> FixResult:
    converted = 0
    skipped = 0

    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        for page in pdf.pages:
            resources = page.get("/Resources")
            if not resources:
                continue
            xobjects = resources.get("/XObject")
            if not xobjects:
                continue

            try:
                xobj_dict = dict(xobjects)
            except Exception:
                continue

            for name, xobj in xobj_dict.items():
                try:
                    if xobj.get("/Subtype") != "/Image":
                        continue
                    cs = str(xobj.get("/ColorSpace", ""))
                    if "RGB" not in cs:
                        continue

                    img_w = int(xobj.get("/Width", 0))
                    img_h = int(xobj.get("/Height", 0))
                    if img_w == 0 or img_h == 0:
                        skipped += 1
                        continue

                    # Read decompressed image bytes
                    raw = xobj.read_bytes()

                    # pikepdf may return bytes for various filter types;
                    # try to construct a PIL image from raw RGB data first,
                    # fall back to stream-based decode
                    try:
                        img = Image.frombytes("RGB", (img_w, img_h), raw)
                    except Exception:
                        try:
                            img = Image.open(io.BytesIO(raw)).convert("RGB")
                        except Exception:
                            skipped += 1
                            continue

                    cmyk = img.convert("CMYK")
                    cmyk_bytes = cmyk.tobytes()

                    # Replace the stream content and colorspace
                    xobj.write(cmyk_bytes, filter=pikepdf.Name("/FlateDecode"))
                    xobj[pikepdf.Name("/ColorSpace")] = pikepdf.Name("/DeviceCMYK")
                    converted += 1

                except Exception as exc:
                    logger.debug(f"Job {job_id}: skipping image {name}: {exc}")
                    skipped += 1
                    continue

        pdf.save(pdf_path)

    if converted == 0:
        return FixResult(
            tool_name="convert_colorspace",
            job_id=job_id,
            success=True,
            description=f"No convertible RGB images found (skipped {skipped})",
        )

    return FixResult(
        tool_name="convert_colorspace",
        job_id=job_id,
        success=True,
        description=f"Converted {converted} image(s) from RGB to CMYK (skipped {skipped})",
        after_value=target_colorspace.upper(),
    )


async def check_image_dpi(
    pdf_path: str,
    job_id: str,
    min_dpi: int = 150,
) -> FixResult:
    """Report low-DPI images in a PDF. Diagnostic tool — flags but cannot upscale."""
    return await asyncio.to_thread(
        _check_image_dpi_sync, pdf_path, job_id, min_dpi,
    )


def _check_image_dpi_sync(
    pdf_path: str,
    job_id: str,
    min_dpi: int,
) -> FixResult:
    low_dpi: list[str] = []

    with pikepdf.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            resources = page.get("/Resources")
            if not resources:
                continue
            xobjects = resources.get("/XObject")
            if not xobjects:
                continue

            mb = page.mediabox
            page_w_pt = float(mb[2]) - float(mb[0])
            page_h_pt = float(mb[3]) - float(mb[1])

            try:
                xobj_dict = dict(xobjects)
            except Exception:
                continue

            for name, xobj in xobj_dict.items():
                try:
                    if xobj.get("/Subtype") != "/Image":
                        continue
                    img_w = int(xobj.get("/Width", 0))
                    img_h = int(xobj.get("/Height", 0))
                    if img_w == 0 or img_h == 0:
                        continue

                    dpi_x = img_w / (page_w_pt / _PT_PER_INCH)
                    dpi_y = img_h / (page_h_pt / _PT_PER_INCH)
                    effective_dpi = min(dpi_x, dpi_y)

                    if effective_dpi < min_dpi:
                        low_dpi.append(
                            f"page {page_num}: {name} ~{effective_dpi:.0f} DPI"
                        )
                except Exception:
                    continue

    if not low_dpi:
        return FixResult(
            tool_name="check_image_dpi",
            job_id=job_id,
            success=True,
            description=f"All images meet the {min_dpi} DPI threshold",
        )

    return FixResult(
        tool_name="check_image_dpi",
        job_id=job_id,
        success=True,
        description=(
            f"Found {len(low_dpi)} low-DPI image(s) below {min_dpi} DPI: "
            + "; ".join(low_dpi[:10])
            + ("..." if len(low_dpi) > 10 else "")
        ),
        after_value=f"{len(low_dpi)} low-DPI images flagged",
    )


# ── DOCX image resize ──────────────────────────────────────────────────

_EMU_PER_INCH = 914400

_DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_DRAWING_NS_2010 = "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


async def resize_images_to_fit(
    file_path: str,
    job_id: str,
    max_width_pct: float = 100.0,
    max_height_pct: float = 90.0,
) -> FixResult:
    """Proportionally resize images in a DOCX that exceed the printable area.

    Images are scaled down so their width does not exceed *max_width_pct* % of
    the printable width and their height does not exceed *max_height_pct* % of
    the printable height.  Aspect ratio is always preserved.

    This fixes the common "image outside page borders" problem without
    distorting visuals.
    """
    return await asyncio.to_thread(
        _resize_images_to_fit_sync, file_path, job_id,
        max_width_pct, max_height_pct,
    )


def _resize_images_to_fit_sync(
    file_path: str,
    job_id: str,
    max_width_pct: float,
    max_height_pct: float,
) -> FixResult:
    from docx import Document

    try:
        doc = Document(file_path)

        if not doc.sections:
            return FixResult(
                tool_name="resize_images_to_fit",
                job_id=job_id,
                success=False,
                description="No sections found in document",
                error="No sections",
            )

        # Calculate printable area from first section
        section = doc.sections[0]
        page_width = section.page_width or 0
        page_height = section.page_height or 0
        left_margin = section.left_margin or 0
        right_margin = section.right_margin or 0
        top_margin = section.top_margin or 0
        bottom_margin = section.bottom_margin or 0

        printable_width = page_width - left_margin - right_margin
        printable_height = page_height - top_margin - bottom_margin

        if printable_width <= 0 or printable_height <= 0:
            return FixResult(
                tool_name="resize_images_to_fit",
                job_id=job_id,
                success=False,
                description="Could not determine printable area dimensions",
                error="Invalid printable dimensions",
            )

        max_w = int(printable_width * (max_width_pct / 100.0))
        max_h = int(printable_height * (max_height_pct / 100.0))

        resized = 0
        total_checked = 0

        # Process all drawings in the document body XML
        body = doc.element.body
        drawings = body.findall(
            ".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing"
        )

        for drawing in drawings:
            # Handle both inline and anchor images
            for tag in ("inline", "anchor"):
                for ns in (_DRAWING_NS, _DRAWING_NS_2010):
                    element = drawing.find(f"{{{ns}}}{tag}")
                    if element is not None:
                        did_resize = _resize_drawing_element(
                            element, max_w, max_h, ns,
                        )
                        total_checked += 1
                        if did_resize:
                            resized += 1

        doc.save(file_path)

        pw_in = printable_width / _EMU_PER_INCH
        ph_in = printable_height / _EMU_PER_INCH

        return FixResult(
            tool_name="resize_images_to_fit",
            job_id=job_id,
            success=True,
            description=(
                f"Checked {total_checked} image(s), resized {resized} to fit "
                f"within printable area ({pw_in:.2f}\" x {ph_in:.2f}\")"
            ),
            after_value=f"{resized} resized of {total_checked} checked",
        )
    except Exception as exc:
        logger.error(f"Job {job_id}: resize_images_to_fit failed: {exc}")
        return FixResult(
            tool_name="resize_images_to_fit",
            job_id=job_id,
            success=False,
            description=f"Failed to resize images: {exc}",
            error=str(exc),
        )


def _resize_drawing_element(
    element,
    max_w: int,
    max_h: int,
    wp_ns: str,
) -> bool:
    """Resize a single wp:inline or wp:anchor element if it exceeds limits.

    Returns True if the image was resized.
    """
    # Get the extent element (contains cx/cy = width/height in EMU)
    extent = element.find(f"{{{wp_ns}}}extent")
    if extent is None:
        return False

    cx_str = extent.get("cx")
    cy_str = extent.get("cy")
    if not cx_str or not cy_str:
        return False

    cx = int(cx_str)
    cy = int(cy_str)

    if cx <= 0 or cy <= 0:
        return False

    # Calculate scale needed to fit
    scale_x = max_w / cx if cx > max_w else 1.0
    scale_y = max_h / cy if cy > max_h else 1.0
    scale = min(scale_x, scale_y)

    if scale >= 1.0:
        return False  # already fits

    new_cx = int(cx * scale)
    new_cy = int(cy * scale)

    # Update the extent
    extent.set("cx", str(new_cx))
    extent.set("cy", str(new_cy))

    # Also update the graphic's transform extent if present
    # (a:ext inside a:xfrm inside the graphic)
    xfrm = element.find(f".//{{{_A_NS}}}xfrm")
    if xfrm is not None:
        a_ext = xfrm.find(f"{{{_A_NS}}}ext")
        if a_ext is not None:
            a_ext.set("cx", str(new_cx))
            a_ext.set("cy", str(new_cy))

    # Update effectExtent if present (for anchored images)
    eff_ext = element.find(f"{{{wp_ns}}}effectExtent")
    if eff_ext is not None:
        # Keep effect extents but scale them proportionally
        for attr in ("l", "t", "r", "b"):
            val_str = eff_ext.get(attr)
            if val_str:
                try:
                    old_val = int(val_str)
                    eff_ext.set(attr, str(int(old_val * scale)))
                except ValueError:
                    pass

    return True
