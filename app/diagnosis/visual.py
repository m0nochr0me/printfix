"""Visual page inspection using Gemini multimodal models."""

import asyncio
from pathlib import Path

from google.genai.types import (
    Content,
    GenerateContentConfig,
    Part,
    ThinkingConfig,
    ThinkingLevel,
)

from app.core.ai import ai_client
from app.core.config import settings
from app.core.effort import EffortConfig
from app.core.log import logger
from app.core.prompts import get_prompt
from app.core.retry import with_retry
from app.schema.diagnosis import (
    PageDiagnosis,
    PageDiagnosisFindings,
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

    system_prompt = get_prompt("visual_inspection").render(
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

    async def _call_gemini() -> str:
        resp = await ai_client.aio.models.generate_content(
            model=model,
            contents=Content(parts=parts),
            config=GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=PageDiagnosisFindings.model_json_schema(),
                temperature=1.0,  # Recommended for Gemini 3
                top_p=0.9,
                thinking_config=ThinkingConfig(thinking_level=ThinkingLevel.HIGH),
                system_instruction=system_prompt,
            ),
        )
        return resp.text or ""

    raw_text = await with_retry(
        _call_gemini,
        max_retries=settings.AI_API_MAX_RETRIES,
        retryable=(TimeoutError, asyncio.TimeoutError, ConnectionError, OSError),
        label=f"gemini-visual({page_range})",
    )
    findings = PageDiagnosisFindings.model_validate_json(raw_text)  # type: ignore
    return findings.pages
