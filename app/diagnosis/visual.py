"""Visual page inspection using Gemini multimodal models."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from google.genai.types import Content, GenerateContentConfig, Part

from app.core.ai import ai_client
from app.core.effort import EffortConfig
from app.core.log import logger
from app.core.prompts import VISUAL_INSPECTION_PROMPT
from app.schema.diagnosis import (
    DiagnosisIssue,
    IssueSeverity,
    IssueSource,
    IssueType,
    PageDiagnosis,
)

__all__ = ("inspect_pages_visually",)


async def inspect_pages_visually(
    page_image_paths: list[str],
    effort_config: EffortConfig,
    file_type: str,
    job_id: str,
) -> list[PageDiagnosis]:
    """
    Run visual inspection on rendered page images.

    Batches pages according to effort_config.page_batch_size,
    samples pages if effort_config.max_pages_sampled is set,
    and calls the configured Gemini model for each batch.
    """
    selected = _select_pages(page_image_paths, effort_config.max_pages_sampled)
    if not selected:
        return []

    total_pages = len(page_image_paths)
    batches = _make_batches(selected, effort_config.page_batch_size)

    all_pages: list[PageDiagnosis] = []
    for batch in batches:
        try:
            pages = await _inspect_batch(
                model=effort_config.visual_model,
                page_batch=batch,
                file_type=file_type,
                total_pages=total_pages,
            )
            all_pages.extend(pages)
        except Exception:
            page_nums = [p[0] for p in batch]
            logger.exception(f"Job {job_id}: visual inspection failed for pages {page_nums}")
            # Return empty diagnoses for failed pages
            for page_num, _ in batch:
                all_pages.append(PageDiagnosis(page=page_num))

    return all_pages


def _select_pages(
    all_paths: list[str],
    max_sampled: int | None,
) -> list[tuple[int, str]]:
    """Select which pages to inspect. Returns list of (page_num, image_path)."""
    indexed = [(i + 1, p) for i, p in enumerate(all_paths)]
    if max_sampled is None or len(indexed) <= max_sampled:
        return indexed

    # Sample: first, last, and evenly-spaced pages
    if max_sampled <= 2:
        return [indexed[0], indexed[-1]]

    result: list[tuple[int, str]] = [indexed[0]]
    inner_count = max_sampled - 2
    step = (len(indexed) - 2) / (inner_count + 1)
    for i in range(1, inner_count + 1):
        idx = int(i * step)
        result.append(indexed[idx])
    result.append(indexed[-1])
    return result


def _make_batches(
    pages: list[tuple[int, str]],
    batch_size: int,
) -> list[list[tuple[int, str]]]:
    """Split pages into batches of the given size."""
    return [pages[i : i + batch_size] for i in range(0, len(pages), batch_size)]


async def _inspect_batch(
    model: str,
    page_batch: list[tuple[int, str]],
    file_type: str,
    total_pages: int,
) -> list[PageDiagnosis]:
    """Send a batch of page images to Gemini and parse the response."""
    page_numbers = [p[0] for p in page_batch]
    page_range = ", ".join(str(n) for n in page_numbers)

    prompt_text = VISUAL_INSPECTION_PROMPT.format(
        page_range=page_range,
        file_type=file_type,
        total_pages=total_pages,
    )

    # Build multimodal content: images + prompt
    parts: list[Part] = []
    for page_num, img_path in page_batch:
        image_bytes = await asyncio.to_thread(Path(img_path).read_bytes)
        parts.append(Part.from_bytes(data=image_bytes, mime_type="image/png"))
        parts.append(Part.from_text(text=f"[Page {page_num}]"))
    parts.append(Part.from_text(text=prompt_text))

    from app.core.config import settings
    from app.core.retry import with_retry

    async def _call_gemini() -> str:
        resp = await asyncio.wait_for(
            asyncio.to_thread(
                ai_client.models.generate_content,
                model=model,
                contents=Content(parts=parts),
                config=GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            ),
            timeout=settings.AI_API_TIMEOUT_SECONDS,
        )
        return resp.text or ""

    raw_text = await with_retry(
        _call_gemini,
        max_retries=settings.AI_API_MAX_RETRIES,
        retryable=(TimeoutError, asyncio.TimeoutError, ConnectionError, OSError),
        label=f"gemini-visual({page_range})",
    )
    return _parse_visual_response(raw_text, page_numbers)


def _parse_visual_response(
    raw_json: str,
    page_numbers: list[int],
) -> list[PageDiagnosis]:
    """Parse and validate Gemini's JSON response into PageDiagnosis objects."""
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse Gemini JSON response: {raw_json[:200]}")
        return [PageDiagnosis(page=n) for n in page_numbers]

    pages_data = data.get("pages", [])
    result: list[PageDiagnosis] = []
    seen_pages: set[int] = set()

    for page_entry in pages_data:
        page_num = page_entry.get("page")
        if page_num not in page_numbers:
            continue
        seen_pages.add(page_num)

        issues: list[DiagnosisIssue] = []
        for issue_data in page_entry.get("issues", []):
            issue = _parse_issue(issue_data, page_num)
            if issue:
                issues.append(issue)

        result.append(PageDiagnosis(page=page_num, issues=issues))

    # Add empty diagnoses for pages not in response
    for pn in page_numbers:
        if pn not in seen_pages:
            result.append(PageDiagnosis(page=pn))

    result.sort(key=lambda p: p.page)
    return result


def _parse_issue(issue_data: dict, page_num: int) -> DiagnosisIssue | None:
    """Parse a single issue from the Gemini response, with validation."""
    try:
        issue_type_raw = issue_data.get("type", "")
        try:
            issue_type = IssueType(issue_type_raw)
        except ValueError:
            logger.debug(f"Unknown issue type from Gemini: {issue_type_raw}")
            return None

        severity_raw = issue_data.get("severity", "warning")
        try:
            severity = IssueSeverity(severity_raw)
        except ValueError:
            severity = IssueSeverity.warning

        confidence = issue_data.get("confidence", 0.7)
        if not isinstance(confidence, (int, float)):
            confidence = 0.7
        confidence = max(0.0, min(1.0, float(confidence)))

        return DiagnosisIssue(
            type=issue_type,
            severity=severity,
            source=IssueSource.visual,
            page=page_num,
            location=issue_data.get("location"),
            description=issue_data.get("description", "Visual issue detected"),
            suggested_fix=issue_data.get("suggested_fix"),
            confidence=confidence,
        )
    except Exception:
        logger.debug(f"Failed to parse issue: {issue_data}")
        return None
