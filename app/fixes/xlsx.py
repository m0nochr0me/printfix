"""XLSX fix tools: margins, page setup, and auto-fit."""

from __future__ import annotations

import asyncio

from app.core.log import logger
from app.schema.fix import FixResult

__all__ = ("set_xlsx_margins", "set_xlsx_page_setup", "auto_fit_xlsx_columns")


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


async def auto_fit_xlsx_columns(
    file_path: str,
    job_id: str,
    max_col_width: float = 30.0,
    min_col_width: float = 5.0,
    shrink_margins: bool = True,
) -> FixResult:
    """Analyse content widths across all sheets and auto-size columns so the
    table fits within a single page width.  When *shrink_margins* is True the
    page margins are also tightened to 0.4" to maximise printable area.

    The function automatically picks portrait or landscape orientation
    depending on which one allows the columns to print without scaling, and
    enables fit-to-page as a safety net.
    """
    return await asyncio.to_thread(
        _auto_fit_xlsx_columns_sync, file_path, job_id,
        max_col_width, min_col_width, shrink_margins,
    )


# Standard paper sizes in inches (width, height) portrait
_PAPER_SIZES: dict[int, tuple[float, float]] = {
    1: (8.5, 11.0),    # Letter
    9: (8.27, 11.69),   # A4
}


def _auto_fit_xlsx_columns_sync(
    file_path: str,
    job_id: str,
    max_col_width: float,
    min_col_width: float,
    shrink_margins: bool,
) -> FixResult:
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    try:
        wb = load_workbook(file_path)
        sheets_changed = 0
        details: list[str] = []

        for ws in wb.worksheets:
            max_col = ws.max_column or 0
            if max_col == 0:
                continue

            # ── 1. Tighten margins if requested ──────────────────────
            margin = 0.4  # inches
            if shrink_margins:
                pm = ws.page_margins
                pm.top = margin
                pm.bottom = margin
                pm.left = margin
                pm.right = margin

            # ── 2. Measure optimal column widths from cell content ───
            optimal_widths: dict[int, float] = {}
            for col_idx in range(1, max_col + 1):
                best = min_col_width
                for row in ws.iter_rows(
                    min_col=col_idx, max_col=col_idx,
                    max_row=min(ws.max_row or 0, 500),
                    values_only=False,
                ):
                    cell = row[0]
                    if cell.value is not None:
                        text = str(cell.value)
                        # Approximate character width (openpyxl units ≈ chars)
                        cell_width = len(text) + 2  # small padding
                        if cell.font and cell.font.bold:
                            cell_width *= 1.1
                        best = max(best, min(cell_width, max_col_width))
                optimal_widths[col_idx] = best

            total_content_chars = sum(optimal_widths.values())

            # ── 3. Choose orientation ────────────────────────────────
            ps = ws.page_setup
            paper_id = ps.paperSize or 1
            page_w_p, page_h_p = _PAPER_SIZES.get(paper_id, (8.5, 11.0))

            left_m = margin if shrink_margins else (ws.page_margins.left or 0.75)
            right_m = margin if shrink_margins else (ws.page_margins.right or 0.75)

            printable_portrait = page_w_p - left_m - right_m
            printable_landscape = page_h_p - left_m - right_m

            # openpyxl column width 1 unit ≈ 1/7 inch
            char_to_inch = 1 / 7.0
            total_content_inches = total_content_chars * char_to_inch

            if total_content_inches <= printable_portrait:
                chosen_orientation = "portrait"
                printable = printable_portrait
            else:
                chosen_orientation = "landscape"
                printable = printable_landscape

            ps.orientation = chosen_orientation
            if chosen_orientation == "landscape":
                # openpyxl doesn't swap for us, so just set orientation
                pass

            # ── 4. Scale columns to fit printable width ──────────────
            printable_chars = printable / char_to_inch
            if total_content_chars > printable_chars:
                scale = printable_chars / total_content_chars
                for col_idx in optimal_widths:
                    optimal_widths[col_idx] = max(
                        min_col_width, optimal_widths[col_idx] * scale,
                    )

            # Apply widths
            for col_idx, w in optimal_widths.items():
                letter = get_column_letter(col_idx)
                ws.column_dimensions[letter].width = w

            # ── 5. Enable fit-to-page as safety net ──────────────────
            ps.fitToPage = True
            ps.fitToWidth = 1
            ps.fitToHeight = 0  # auto height

            sheets_changed += 1
            details.append(
                f"'{ws.title}': {max_col} cols, {chosen_orientation}, "
                f"margins {margin}\""
            )

        wb.save(file_path)
        wb.close()

        return FixResult(
            tool_name="auto_fit_xlsx_columns",
            job_id=job_id,
            success=True,
            description=(
                f"Auto-fitted columns on {sheets_changed} sheet(s): "
                + "; ".join(details)
            ),
            after_value="; ".join(details),
        )
    except Exception as exc:
        logger.error(f"Job {job_id}: auto_fit_xlsx_columns failed: {exc}")
        return FixResult(
            tool_name="auto_fit_xlsx_columns",
            job_id=job_id,
            success=False,
            description=f"Failed to auto-fit XLSX columns: {exc}",
            error=str(exc),
        )
