"""Image fix tools: colorspace conversion and DPI reporting."""

from __future__ import annotations

import asyncio
import io

import pikepdf
from PIL import Image

from app.core.log import logger
from app.schema.fix import FixResult

__all__ = ("check_image_dpi", "convert_pdf_colorspace")

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
