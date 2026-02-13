"""Structural analysis for PDF files using pikepdf."""

import asyncio

import pikepdf

from app.core.config import settings
from app.core.log import logger
from app.schema.diagnosis import (
    DiagnosisIssue,
    IssueSeverity,
    IssueSource,
    IssueType,
)

__all__ = ("analyze_pdf",)

_PT_PER_INCH = 72.0


async def analyze_pdf(pdf_path: str, job_id: str) -> list[DiagnosisIssue]:
    """Perform structural analysis on a PDF file."""
    logger.info(f"Job {job_id}: running PDF structural analysis on {pdf_path}")
    return await asyncio.to_thread(_analyze_pdf_sync, pdf_path, job_id)


def _analyze_pdf_sync(pdf_path: str, job_id: str) -> list[DiagnosisIssue]:
    issues: list[DiagnosisIssue] = []
    try:
        with pikepdf.open(pdf_path) as pdf:
            issues.extend(_check_page_sizes(pdf))
            issues.extend(_check_fonts(pdf))
            issues.extend(_check_images(pdf))
            issues.extend(_check_colorspaces(pdf))
            issues.extend(_check_crop_boxes(pdf))
    except Exception:
        logger.exception(f"Job {job_id}: PDF structural analysis failed")
    return issues


def _check_page_sizes(pdf: pikepdf.Pdf) -> list[DiagnosisIssue]:
    """Check for inconsistent page sizes across the document."""
    issues: list[DiagnosisIssue] = []
    if len(pdf.pages) < 2:
        return issues

    sizes: list[tuple[float, float]] = []
    for page in pdf.pages:
        mb = page.mediabox
        w = float(mb[2]) - float(mb[0])
        h = float(mb[3]) - float(mb[1])
        sizes.append((round(w, 1), round(h, 1)))

    first_size = sizes[0]
    mismatched = [i + 1 for i, s in enumerate(sizes) if s != first_size]
    if mismatched:
        issues.append(
            DiagnosisIssue(
                type=IssueType.page_size_mismatch,
                severity=IssueSeverity.warning,
                source=IssueSource.structural,
                description=(
                    f"Inconsistent page sizes: pages {mismatched} differ from "
                    f"page 1 ({first_size[0] / 72:.1f}x{first_size[1] / 72:.1f} in)"
                ),
                suggested_fix="set_page_size",
                confidence=0.95,
            )
        )

    return issues


def _check_fonts(pdf: pikepdf.Pdf) -> list[DiagnosisIssue]:
    """Check for non-embedded fonts."""
    issues: list[DiagnosisIssue] = []
    non_embedded: set[str] = set()

    for _page_num, page in enumerate(pdf.pages, 1):
        resources = page.get("/Resources")
        if not resources:
            continue
        fonts = resources.get("/Font")
        if not fonts:
            continue

        try:
            font_dict = dict(fonts)
        except Exception:
            continue

        for font_name, font_ref in font_dict.items():
            try:
                font_obj = font_ref if isinstance(font_ref, pikepdf.Dictionary) else pikepdf.Dictionary(font_ref)
                descriptor = font_obj.get("/FontDescriptor")
                if descriptor is None:
                    # Type1/Type3 base fonts without descriptors are usually built-in
                    base_font = str(font_obj.get("/BaseFont", ""))
                    if base_font and not _is_standard_font(base_font):
                        non_embedded.add(base_font)
                    continue

                has_file = "/FontFile" in descriptor or "/FontFile2" in descriptor or "/FontFile3" in descriptor
                if not has_file:
                    base_font = str(font_obj.get("/BaseFont", font_name))
                    non_embedded.add(base_font)
            except Exception:
                continue

    for font_name in non_embedded:
        issues = [
            *issues,
            DiagnosisIssue(
                type=IssueType.non_embedded_font,
                severity=IssueSeverity.critical,
                source=IssueSource.structural,
                description=f"Font '{font_name}' is not embedded — may render differently on print server",
                suggested_fix="embed_fonts",
                confidence=0.9,
            ),
        ]

    return issues


def _check_images(pdf: pikepdf.Pdf) -> list[DiagnosisIssue]:
    """Check image DPI relative to page placement."""
    issues: list[DiagnosisIssue] = []
    min_dpi = settings.DIAGNOSIS_MIN_IMAGE_DPI

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

        for name, xobj_ref in xobj_dict.items():
            try:
                xobj = xobj_ref
                if xobj.get("/Subtype") != "/Image":
                    continue

                img_w = int(xobj.get("/Width", 0))
                img_h = int(xobj.get("/Height", 0))
                if img_w == 0 or img_h == 0:
                    continue

                # Estimate DPI assuming image spans full page width
                # (conservative — actual placement may be smaller)
                dpi_x = img_w / (page_w_pt / _PT_PER_INCH)
                dpi_y = img_h / (page_h_pt / _PT_PER_INCH)
                effective_dpi = min(dpi_x, dpi_y)

                if effective_dpi < min_dpi:
                    # severity = (
                    #     IssueSeverity.critical if effective_dpi < 72
                    #     else IssueSeverity.warning
                    # )
                    issues.append(
                        DiagnosisIssue(
                            type=IssueType.low_dpi_image,
                            severity=IssueSeverity.info,
                            source=IssueSource.structural,
                            page=page_num,
                            description=(
                                f"Image '{name}' has ~{effective_dpi:.0f} DPI (minimum {min_dpi} recommended for print)"
                            ),
                            suggested_fix="check_image_dpi",
                            confidence=0.7,
                        )
                    )
            except Exception:
                continue

    return issues


def _check_colorspaces(pdf: pikepdf.Pdf) -> list[DiagnosisIssue]:
    """Check for RGB color spaces (should be CMYK for professional print)."""
    issues: list[DiagnosisIssue] = []
    has_rgb = False

    for page in pdf.pages:
        resources = page.get("/Resources")
        if not resources:
            continue

        # Check XObject images for colorspace
        xobjects = resources.get("/XObject")
        if xobjects:
            try:
                for xobj_ref in dict(xobjects).values():
                    xobj = xobj_ref
                    if xobj.get("/Subtype") != "/Image":
                        continue
                    cs = str(xobj.get("/ColorSpace", ""))
                    if "RGB" in cs:
                        has_rgb = True
                        break
            except Exception:
                continue

        if has_rgb:
            break

    if has_rgb:
        issues.append(
            DiagnosisIssue(
                type=IssueType.rgb_colorspace,
                severity=IssueSeverity.info,
                source=IssueSource.structural,
                description="Document contains RGB images — CMYK recommended for professional print",
                suggested_fix="convert_colorspace",
                confidence=0.9,
            )
        )

    return issues


def _check_crop_boxes(pdf: pikepdf.Pdf) -> list[DiagnosisIssue]:
    """Check if crop boxes differ significantly from media boxes."""
    issues: list[DiagnosisIssue] = []

    for page_num, page in enumerate(pdf.pages, 1):
        mb = page.mediabox
        cb = page.get("/CropBox")
        if cb is None:
            continue

        mb_w = float(mb[2]) - float(mb[0])
        mb_h = float(mb[3]) - float(mb[1])
        cb_w = float(cb[2]) - float(cb[0])
        cb_h = float(cb[3]) - float(cb[1])

        # Flag if crop box is significantly smaller (> 5% reduction)
        if mb_w > 0 and mb_h > 0:
            w_ratio = cb_w / mb_w
            h_ratio = cb_h / mb_h
            if w_ratio < 0.95 or h_ratio < 0.95:
                issues.append(
                    DiagnosisIssue(
                        type=IssueType.clipped_content,
                        severity=IssueSeverity.warning,
                        source=IssueSource.structural,
                        page=page_num,
                        description=(
                            f"CropBox is significantly smaller than MediaBox "
                            f"({w_ratio:.0%} width, {h_ratio:.0%} height) — "
                            f"content may be hidden"
                        ),
                        suggested_fix="pdf_crop_margins",
                        confidence=0.8,
                    )
                )

    return issues


def _is_standard_font(base_font: str) -> bool:
    """Check if a font is one of the PDF standard 14 fonts."""
    standard = {
        "/Courier",
        "/Courier-Bold",
        "/Courier-Oblique",
        "/Courier-BoldOblique",
        "/Helvetica",
        "/Helvetica-Bold",
        "/Helvetica-Oblique",
        "/Helvetica-BoldOblique",
        "/Times-Roman",
        "/Times-Bold",
        "/Times-Italic",
        "/Times-BoldItalic",
        "/Symbol",
        "/ZapfDingbats",
    }
    return base_font in standard or base_font.lstrip("/") in {s.lstrip("/") for s in standard}
