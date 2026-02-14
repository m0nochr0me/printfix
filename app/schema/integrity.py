"""
File integrity check result models.
"""

from enum import StrEnum

from pydantic import BaseModel


class IntegrityStatus(StrEnum):
    valid = "valid"
    repaired = "repaired"
    corrupt = "corrupt"
    unknown = "unknown"


class IntegrityResult(BaseModel):
    """Result of a file integrity check."""

    file_path: str
    file_type: str
    status: IntegrityStatus
    valid: bool
    details: str = ""
    magic_bytes_ok: bool | None = None
    structure_ok: bool | None = None
    repaired: bool = False
    repair_method: str | None = None
