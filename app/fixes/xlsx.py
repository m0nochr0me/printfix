"""XLSX fix tools: margins and page setup."""

from __future__ import annotations

import asyncio

from app.core.log import logger
from app.schema.fix import FixResult

__all__ = ("set_xlsx_margins", "set_xlsx_page_setup")


async def set_xlsx_margins(
    file_path: str,
    job_id: str,
    top: float = 0.75,
    bottom: float = 0.75,
    left: float = 0.75,
    right: float = 0.75,
) -> FixResult:
    """Set print margins on all sheets. Values in inches."""
    return await asyncio.to_thread(
        _set_xlsx_margins_sync, file_path, job_id, top, bottom, left, right,
    )


def _set_xlsx_margins_sync(
    file_path: str,
    job_id: str,
    top: float,
    bottom: float,
    left: float,
    right: float,
) -> FixResult:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(file_path)
        sheets_changed = 0

        for ws in wb.worksheets:
            pm = ws.page_margins
            changed = False
            for attr, val in [("top", top), ("bottom", bottom),
                              ("left", left), ("right", right)]:
                if getattr(pm, attr) != val:
                    setattr(pm, attr, val)
                    changed = True
            if changed:
                sheets_changed += 1

        wb.save(file_path)
        wb.close()

        return FixResult(
            tool_name="set_xlsx_margins",
            job_id=job_id,
            success=True,
            description=f"Set margins on {sheets_changed} sheet(s) to "
                        f"T={top} B={bottom} L={left} R={right} inches",
            after_value=f"T={top} B={bottom} L={left} R={right}",
        )
    except Exception as exc:
        logger.error(f"Job {job_id}: set_xlsx_margins failed: {exc}")
        return FixResult(
            tool_name="set_xlsx_margins",
            job_id=job_id,
            success=False,
            description=f"Failed to set XLSX margins: {exc}",
            error=str(exc),
        )


async def set_xlsx_page_setup(
    file_path: str,
    job_id: str,
    orientation: str = "portrait",
    paper_size: int = 1,
    fit_to_page: bool = True,
) -> FixResult:
    """Configure print page setup on all sheets.

    Args:
        orientation: 'portrait' or 'landscape'
        paper_size: 1=Letter, 9=A4
        fit_to_page: if True, fit all columns to one page width
    """
    return await asyncio.to_thread(
        _set_xlsx_page_setup_sync, file_path, job_id,
        orientation, paper_size, fit_to_page,
    )


def _set_xlsx_page_setup_sync(
    file_path: str,
    job_id: str,
    orientation: str,
    paper_size: int,
    fit_to_page: bool,
) -> FixResult:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(file_path)
        sheets_changed = 0

        for ws in wb.worksheets:
            ps = ws.page_setup
            ps.orientation = orientation
            ps.paperSize = paper_size

            if fit_to_page:
                ps.fitToPage = True
                ps.fitToWidth = 1
                ps.fitToHeight = 0  # 0 = auto (fit width, allow multiple pages tall)

            sheets_changed += 1

        wb.save(file_path)
        wb.close()

        paper_name = {1: "Letter", 9: "A4"}.get(paper_size, str(paper_size))
        return FixResult(
            tool_name="set_xlsx_page_setup",
            job_id=job_id,
            success=True,
            description=(
                f"Set page setup on {sheets_changed} sheet(s): "
                f"{orientation}, {paper_name}"
                + (", fit-to-page enabled" if fit_to_page else "")
            ),
            after_value=f"{orientation}, {paper_name}, fit={fit_to_page}",
        )
    except Exception as exc:
        logger.error(f"Job {job_id}: set_xlsx_page_setup failed: {exc}")
        return FixResult(
            tool_name="set_xlsx_page_setup",
            job_id=job_id,
            success=False,
            description=f"Failed to set XLSX page setup: {exc}",
            error=str(exc),
        )
