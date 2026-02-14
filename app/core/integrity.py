"""
File integrity validation, LibreOffice repair, and backup/restore utilities.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import zipfile
import zlib
from pathlib import Path

import pikepdf
from PIL import Image

from app.core.config import settings
from app.core.log import logger
from app.schema.integrity import IntegrityResult, IntegrityStatus

__all__ = (
    "attempt_libreoffice_repair",
    "cleanup_backup",
    "create_backup",
    "restore_from_backup",
    "validate_after_fix",
    "validate_file",
)

# ── Magic bytes ──────────────────────────────────────────────────────────

_MAGIC_BYTES: dict[str, bytes] = {
    ".pdf": b"%PDF-",
    ".docx": b"PK\x03\x04",
    ".xlsx": b"PK\x03\x04",
    ".pptx": b"PK\x03\x04",
    ".odt": b"PK\x03\x04",
    ".ods": b"PK\x03\x04",
    ".odp": b"PK\x03\x04",
}

_OOXML_REQUIRED_ENTRIES: dict[str, list[str]] = {
    ".docx": ["word/document.xml", "[Content_Types].xml"],
    ".xlsx": ["xl/workbook.xml", "[Content_Types].xml"],
    ".pptx": ["ppt/presentation.xml", "[Content_Types].xml"],
}

_ODF_REQUIRED_ENTRIES: dict[str, list[str]] = {
    ".odt": ["content.xml", "META-INF/manifest.xml"],
    ".ods": ["content.xml", "META-INF/manifest.xml"],
    ".odp": ["content.xml", "META-INF/manifest.xml"],
}

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff"}

# LibreOffice infilter strings for repair mode
_LO_REPAIR_FILTERS: dict[str, str] = {
    ".docx": "Microsoft Word 2007-2019 XML",
    ".xlsx": "Calc MS Excel 2007 XML",
    ".pptx": "Impress MS PowerPoint 2007 XML",
    ".odt": "writer8",
    ".ods": "calc8",
    ".odp": "impress8",
}

# Mapping from extension to LibreOffice --convert-to format for same-format output
_LO_CONVERT_FORMATS: dict[str, str] = {
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
    ".odt": "odt",
    ".ods": "ods",
    ".odp": "odp",
}


# ── Sync validation helpers (run in thread) ──────────────────────────────


def _check_magic_bytes(file_path: str, ext: str) -> bool:
    expected = _MAGIC_BYTES.get(ext)
    if expected is None:
        return True  # no known signature to check
    with open(file_path, "rb") as f:
        header = f.read(len(expected))
    return header == expected


def _check_zip_structure(file_path: str, ext: str) -> tuple[bool, str]:
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            bad = zf.testzip()
            if bad is not None:
                return False, f"corrupt entry in ZIP archive: {bad}"

            namelist = zf.namelist()

            # Check OOXML required entries
            required = _OOXML_REQUIRED_ENTRIES.get(ext) or _ODF_REQUIRED_ENTRIES.get(ext)
            if required:
                missing = [e for e in required if e not in namelist]
                if missing:
                    return False, f"missing required entries: {', '.join(missing)}"

        return True, "valid ZIP structure"
    except zipfile.BadZipFile as exc:
        return False, f"invalid ZIP archive: {exc}"
    except Exception as exc:
        return False, f"ZIP check error: {exc}"


def _check_pdf_structure(file_path: str) -> tuple[bool, str]:
    try:
        with pikepdf.open(file_path) as pdf:
            page_count = len(pdf.pages)
            if page_count == 0:
                return False, "PDF has 0 pages"
            # Force parsing of at least page 0
            _ = pdf.pages[0].mediabox
        return True, f"valid PDF with {page_count} pages"
    except pikepdf.PdfError as exc:
        return False, f"PDF structure error: {exc}"
    except Exception as exc:
        return False, f"PDF check error: {exc}"


def _check_image_structure(file_path: str) -> tuple[bool, str]:
    try:
        with Image.open(file_path) as img:
            img.verify()
        return True, "valid image"
    except Exception as exc:
        return False, f"image validation failed: {exc}"


def _validate_file_sync(file_path: str, file_type: str) -> IntegrityResult:
    """Synchronous file validation — dispatches to format-specific checkers."""
    if not os.path.exists(file_path):
        return IntegrityResult(
            file_path=file_path,
            file_type=file_type,
            status=IntegrityStatus.corrupt,
            valid=False,
            details="file does not exist",
        )

    if os.path.getsize(file_path) == 0:
        return IntegrityResult(
            file_path=file_path,
            file_type=file_type,
            status=IntegrityStatus.corrupt,
            valid=False,
            details="file is empty (0 bytes)",
        )

    # Magic bytes check
    magic_ok = _check_magic_bytes(file_path, file_type)

    # Format-specific structure check
    structure_ok = True
    details = ""

    if file_type == ".pdf":
        structure_ok, details = _check_pdf_structure(file_path)
    elif file_type in _OOXML_REQUIRED_ENTRIES or file_type in _ODF_REQUIRED_ENTRIES:
        structure_ok, details = _check_zip_structure(file_path, file_type)
    elif file_type in _IMAGE_EXTENSIONS:
        structure_ok, details = _check_image_structure(file_path)
    else:
        details = "no structural check available for this format"

    valid = magic_ok and structure_ok
    status = IntegrityStatus.valid if valid else IntegrityStatus.corrupt

    if not magic_ok:
        details = f"magic bytes mismatch for {file_type}; {details}"

    return IntegrityResult(
        file_path=file_path,
        file_type=file_type,
        status=status,
        valid=valid,
        details=details,
        magic_bytes_ok=magic_ok,
        structure_ok=structure_ok,
    )


# ── ZIP repack (CRC recovery) ────────────────────────────────────────────

_ZIP_BASED_EXTENSIONS = set(_OOXML_REQUIRED_ENTRIES) | set(_ODF_REQUIRED_ENTRIES)


def _repack_zip_sync(file_path: str) -> tuple[bool, str]:
    """
    Repack a ZIP archive to fix CRC and structural errors.

    Reads each entry from the source ZIP (bypassing CRC checks on failure)
    and writes them into a fresh ZIP with correct checksums. Replaces the
    original file on success.
    """
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=Path(file_path).suffix)
    os.close(tmp_fd)

    try:
        repacked = 0
        skipped = 0

        with (
            zipfile.ZipFile(file_path, "r") as src,
            zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as dst,
        ):
            for info in src.infolist():
                # Skip directory entries
                if info.is_dir():
                    continue

                data: bytes | None = None

                # Try normal read first (with CRC verification)
                try:
                    data = src.read(info.filename)
                except zipfile.BadZipFile, zlib.error, OSError:
                    # CRC mismatch or decompression error — bypass CRC check
                    try:
                        zef = src.open(info)
                        zef._expected_crc = None  # type: ignore[attr-defined]
                        data = zef.read()
                        zef.close()
                    except Exception:
                        skipped += 1
                        continue

                if data is not None:
                    dst.writestr(info, data)
                    repacked += 1

        if repacked == 0:
            os.remove(tmp_path)
            return False, "no entries could be recovered from ZIP"

        shutil.move(tmp_path, file_path)
        msg = f"repacked {repacked} entries"
        if skipped:
            msg += f" ({skipped} unrecoverable entries skipped)"
        return True, msg

    except Exception as exc:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False, f"ZIP repack failed: {exc}"


# ── Async public API ────────────────────────────────────────────────────


async def validate_file(file_path: str, file_type: str) -> IntegrityResult:
    """Validate a file's integrity. Returns an IntegrityResult."""
    return await asyncio.to_thread(_validate_file_sync, file_path, file_type)


async def attempt_libreoffice_repair(
    input_path: str,
    file_type: str,
    _timeout: int | None = None,
) -> IntegrityResult:
    """
    Attempt to repair a document using LibreOffice's repair mode.

    Overwrites the input file with the repaired version on success.
    """
    if _timeout is None:
        _timeout = settings.LIBREOFFICE_REPAIR_TIMEOUT_SECONDS

    filter_name = _LO_REPAIR_FILTERS.get(file_type)
    convert_format = _LO_CONVERT_FORMATS.get(file_type)

    if not filter_name or not convert_format:
        return IntegrityResult(
            file_path=input_path,
            file_type=file_type,
            status=IntegrityStatus.corrupt,
            valid=False,
            details=f"no repair filter available for {file_type}",
        )

    input_p = Path(input_path)
    output_dir = input_p.parent
    original_stem = input_p.stem

    logger.info(f"Attempting LibreOffice repair for {input_path} ({file_type})")

    # For ZIP-based formats, try repacking first to fix CRC errors
    if file_type in _ZIP_BASED_EXTENSIONS:
        repack_ok, repack_detail = await asyncio.to_thread(_repack_zip_sync, input_path)
        if repack_ok:
            logger.info(f"ZIP repack succeeded for {input_path}: {repack_detail}")
            # Re-validate after repack — may already be fixed
            recheck = await validate_file(input_path, file_type)
            if recheck.valid:
                logger.info(f"File {input_path} is valid after ZIP repack, skipping LibreOffice repair")
                return IntegrityResult(
                    file_path=input_path,
                    file_type=file_type,
                    status=IntegrityStatus.repaired,
                    valid=True,
                    details=f"repaired via ZIP repack ({repack_detail})",
                    magic_bytes_ok=recheck.magic_bytes_ok,
                    structure_ok=recheck.structure_ok,
                    repaired=True,
                    repair_method="zip_repack",
                )
        else:
            logger.warning(f"ZIP repack failed for {input_path}: {repack_detail}, proceeding to LibreOffice repair")

    infilter = f"{filter_name}:repairmode"
    proc = await asyncio.create_subprocess_exec(
        "libreoffice",
        "--headless",
        "--norestore",
        f"--infilter={infilter}",
        "--convert-to",
        convert_format,
        "--outdir",
        str(output_dir),
        input_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_timeout)
    except asyncio.TimeoutError:
        proc.kill()
        logger.error(f"LibreOffice repair timed out after {_timeout}s for {input_path}")
        return IntegrityResult(
            file_path=input_path,
            file_type=file_type,
            status=IntegrityStatus.corrupt,
            valid=False,
            details=f"repair timed out after {_timeout}s",
        )

    if proc.returncode != 0:
        err = stderr.decode().strip()
        logger.error(f"LibreOffice repair failed (exit {proc.returncode}): {err}")
        return IntegrityResult(
            file_path=input_path,
            file_type=file_type,
            status=IntegrityStatus.corrupt,
            valid=False,
            details=f"repair failed (exit {proc.returncode}): {err}",
        )

    # LibreOffice outputs with the same stem — may already be the same file
    repaired_path = output_dir / f"{original_stem}.{convert_format}"
    if not repaired_path.exists():
        return IntegrityResult(
            file_path=input_path,
            file_type=file_type,
            status=IntegrityStatus.corrupt,
            valid=False,
            details="repair produced no output file",
        )

    # If the output path differs from input, overwrite the original
    if str(repaired_path) != input_path:
        await asyncio.to_thread(shutil.move, str(repaired_path), input_path)

    # Validate the repaired file
    check = await validate_file(input_path, file_type)
    if check.valid:
        logger.info(f"LibreOffice repair succeeded for {input_path}")
        return IntegrityResult(
            file_path=input_path,
            file_type=file_type,
            status=IntegrityStatus.repaired,
            valid=True,
            details=f"repaired via LibreOffice ({infilter})",
            magic_bytes_ok=check.magic_bytes_ok,
            structure_ok=check.structure_ok,
            repaired=True,
            repair_method=f"libreoffice:{infilter}",
        )

    logger.error(f"LibreOffice repair produced invalid output for {input_path}: {check.details}")
    return IntegrityResult(
        file_path=input_path,
        file_type=file_type,
        status=IntegrityStatus.corrupt,
        valid=False,
        details=f"repair output still invalid: {check.details}",
    )


# ── Backup / restore ────────────────────────────────────────────────────


async def create_backup(file_path: str) -> str:
    """Create a backup copy of a file. Returns the backup path."""
    backup_path = file_path + ".bak"
    await asyncio.to_thread(shutil.copy2, file_path, backup_path)
    return backup_path


async def restore_from_backup(backup_path: str, original_path: str) -> bool:
    """Restore a file from its backup. Returns True on success."""
    try:
        await asyncio.to_thread(shutil.copy2, backup_path, original_path)
        await asyncio.to_thread(os.remove, backup_path)
        return True
    except OSError as exc:
        logger.error(f"Failed to restore from backup {backup_path}: {exc}")
        return False


async def cleanup_backup(backup_path: str) -> None:
    """Remove a backup file if it exists."""
    try:
        if await asyncio.to_thread(os.path.exists, backup_path):
            await asyncio.to_thread(os.remove, backup_path)
    except OSError:
        pass  # best-effort cleanup


async def validate_after_fix(
    file_path: str,
    file_type: str,
    backup_path: str,
) -> IntegrityResult:
    """
    Validate a file after a fix has been applied.

    If the file is invalid, automatically restores from backup.
    If the file is valid, cleans up the backup.
    """
    result = await validate_file(file_path, file_type)

    if result.valid:
        await cleanup_backup(backup_path)
        return result

    # Auto-restore from backup
    logger.warning(f"Post-fix validation failed for {file_path}: {result.details} — restoring from backup")
    restored = await restore_from_backup(backup_path, file_path)
    if restored:
        result.details = f"fix corrupted file, restored from backup; original error: {result.details}"
    else:
        result.details = f"fix corrupted file and backup restore FAILED; original error: {result.details}"

    return result
