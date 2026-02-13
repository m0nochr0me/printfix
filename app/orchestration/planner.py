"""
Fix planner — rule-based and AI-driven fix plan generation.

Given a DocumentDiagnosis, produces a FixPlan: an ordered list of
FixActions to apply in a single iteration.
"""

import asyncio
import json

import yaml
from google.genai.types import GenerateContentConfig, ThinkingConfig, ThinkingLevel

from app.core.ai import ai_client, extract_anthropic_text, get_anthropic_client
from app.core.config import settings
from app.core.effort import EffortConfig
from app.core.log import logger
from app.core.prompts import get_prompt
from app.core.retry import with_retry
from app.schema.diagnosis import (
    DiagnosisIssue,
    DocumentDiagnosis,
    IssueSeverity,
)
from app.schema.orchestration import FixAction, FixPlan, PlannerFindings

__all__ = ("plan_fixes",)

# ── Issue type → default tool mapping ────────────────────────────────────

_DOCX_ISSUE_MAP: dict[str, list[FixAction]] = {
    "margin_violation": [
        FixAction(
            tool_name="set_margins",
            params={"top": 0.75, "bottom": 0.75, "left": 0.75, "right": 0.75},
            target_issues=["margin_violation"],
            reasoning='Set safe 0.75" margins to prevent content clipping',
        ),
    ],
    "inconsistent_margins": [
        FixAction(
            tool_name="set_margins",
            params={"top": 0.75, "bottom": 0.75, "left": 0.75, "right": 0.75},
            target_issues=["inconsistent_margins"],
            reasoning="Normalize margins across all sections",
        ),
    ],
    "clipped_content": [
        FixAction(
            tool_name="set_margins",
            params={"top": 0.5, "bottom": 0.5, "left": 0.5, "right": 0.5},
            target_issues=["clipped_content"],
            reasoning="Reduce margins to give content more room",
        ),
        FixAction(
            tool_name="adjust_paragraph_indents",
            params={"max_left_inches": 0.5, "max_right_inches": 0.5, "max_first_line_inches": 0.5},
            target_issues=["clipped_content"],
            reasoning="Cap indents that undermine margin reduction",
        ),
        FixAction(
            tool_name="auto_fit_tables",
            params={},
            target_issues=["clipped_content"],
            reasoning="Auto-fit tables that may be causing overflow",
        ),
    ],
    "text_overflow": [
        FixAction(
            tool_name="auto_fit_tables",
            params={},
            target_issues=["text_overflow"],
            reasoning="Auto-fit tables to prevent text overflow",
        ),
        FixAction(
            tool_name="adjust_paragraph_indents",
            params={"max_left_inches": 0.5, "max_right_inches": 0.5, "max_first_line_inches": 0.5},
            target_issues=["text_overflow"],
            reasoning="Reduce indents that may push text beyond printable area",
        ),
    ],
    "table_overflow": [
        FixAction(
            tool_name="auto_fit_tables",
            params={},
            target_issues=["table_overflow"],
            reasoning="Auto-fit tables to page width",
        ),
        FixAction(
            tool_name="resize_table_text",
            params={"table_index": 0, "max_font_size_pt": 9.0},
            target_issues=["table_overflow"],
            reasoning="Reduce table font size to help fit content",
        ),
    ],
    "small_font": [
        FixAction(
            tool_name="adjust_font_size",
            params={"min_size_pt": 10.0},
            target_issues=["small_font"],
            reasoning="Enforce minimum readable font size of 10pt",
        ),
    ],
    "wrong_orientation": [
        FixAction(
            tool_name="set_orientation",
            params={"orientation": "landscape"},
            target_issues=["wrong_orientation"],
            reasoning="Switch to landscape for wide content",
        ),
    ],
    "blank_page": [
        FixAction(
            tool_name="remove_blank_pages",
            params={},
            target_issues=["blank_page"],
            reasoning="Remove accidental blank pages",
        ),
    ],
    "bad_page_break": [
        FixAction(
            tool_name="fix_page_breaks",
            params={"strategy": "remove_consecutive"},
            target_issues=["bad_page_break"],
            reasoning="Remove problematic consecutive page breaks",
        ),
    ],
    "page_size_mismatch": [
        FixAction(
            tool_name="set_page_size",
            params={"width": 8.27, "height": 11.69},  # A4 default
            target_issues=["page_size_mismatch"],
            reasoning="Standardize to A4 page size",
        ),
    ],
    "non_embedded_font": [
        FixAction(
            tool_name="replace_font",
            params={"from_font": "", "to_font": "Arial"},
            target_issues=["non_embedded_font"],
            reasoning="Replace non-embedded font with safe default",
        ),
    ],
    "image_overflow": [
        FixAction(
            tool_name="resize_images_to_fit",
            params={"max_width_pct": 100.0, "max_height_pct": 90.0},
            target_issues=["image_overflow"],
            reasoning="Proportionally resize images that exceed printable area",
        ),
    ],
    "inconsistent_indent": [
        FixAction(
            tool_name="adjust_paragraph_indents",
            params={"max_left_inches": 0.5, "max_right_inches": 0.5, "max_first_line_inches": 0.5},
            target_issues=["inconsistent_indent"],
            reasoning="Cap excessive paragraph indents to reclaim printable space",
        ),
    ],
}

# ── DOCX→PDF fallback mapping ────────────────────────────────────────
# When a DOCX fix fails for a given issue type, these PDF alternatives
# are tried on the reference PDF instead.

_DOCX_TO_PDF_FALLBACK: dict[str, list[FixAction]] = {
    "margin_violation": [
        FixAction(
            tool_name="pdf_crop_margins",
            params={"top": 0.25, "bottom": 0.25, "left": 0.25, "right": 0.25},
            target_issues=["margin_violation"],
            reasoning="DOCX margin fix failed; adjusting PDF crop box as fallback",
            is_fallback=True,
        ),
    ],
    "inconsistent_margins": [
        FixAction(
            tool_name="pdf_crop_margins",
            params={"top": 0.25, "bottom": 0.25, "left": 0.25, "right": 0.25},
            target_issues=["inconsistent_margins"],
            reasoning="DOCX margin normalization failed; using PDF crop box fallback",
            is_fallback=True,
        ),
    ],
    "clipped_content": [
        FixAction(
            tool_name="pdf_scale_content",
            params={"scale_factor": 0.9},
            target_issues=["clipped_content"],
            reasoning="DOCX fixes didn't resolve clipping; scaling PDF content as fallback",
            is_fallback=True,
        ),
    ],
    "text_overflow": [
        FixAction(
            tool_name="pdf_scale_content",
            params={"scale_factor": 0.92},
            target_issues=["text_overflow"],
            reasoning="DOCX fixes didn't resolve overflow; scaling PDF content as fallback",
            is_fallback=True,
        ),
    ],
    "table_overflow": [
        FixAction(
            tool_name="pdf_scale_content",
            params={"scale_factor": 0.85},
            target_issues=["table_overflow"],
            reasoning="DOCX table fixes failed; scaling PDF content as fallback",
            is_fallback=True,
        ),
    ],
    "wrong_orientation": [
        FixAction(
            tool_name="pdf_rotate_pages",
            params={"pages": None, "angle": 90},
            target_issues=["wrong_orientation"],
            reasoning="DOCX orientation fix failed; rotating PDF pages as fallback",
            is_fallback=True,
        ),
    ],
    "image_overflow": [
        FixAction(
            tool_name="pdf_scale_content",
            params={"scale_factor": 0.9},
            target_issues=["image_overflow"],
            reasoning="DOCX margin adjustments didn't resolve image overflow; scaling PDF as fallback",
            is_fallback=True,
        ),
    ],
    "inconsistent_indent": [
        FixAction(
            tool_name="pdf_scale_content",
            params={"scale_factor": 0.92},
            target_issues=["inconsistent_indent"],
            reasoning="DOCX indent adjustment failed; scaling PDF content as fallback",
            is_fallback=True,
        ),
    ],
}

_PDF_ISSUE_MAP: dict[str, list[FixAction]] = {
    "margin_violation": [
        FixAction(
            tool_name="pdf_crop_margins",
            params={"top": 0.25, "bottom": 0.25, "left": 0.25, "right": 0.25},
            target_issues=["margin_violation"],
            reasoning="Adjust PDF crop box to add margin space",
        ),
    ],
    "inconsistent_margins": [
        FixAction(
            tool_name="pdf_crop_margins",
            params={"top": 0.25, "bottom": 0.25, "left": 0.25, "right": 0.25},
            target_issues=["inconsistent_margins"],
            reasoning="Normalize margins via crop box",
        ),
    ],
    "clipped_content": [
        FixAction(
            tool_name="pdf_scale_content",
            params={"scale_factor": 0.9},
            target_issues=["clipped_content"],
            reasoning="Scale content down to fit within page bounds",
        ),
    ],
    "text_overflow": [
        FixAction(
            tool_name="pdf_scale_content",
            params={"scale_factor": 0.92},
            target_issues=["text_overflow"],
            reasoning="Scale content to prevent overflow",
        ),
    ],
    "table_overflow": [
        FixAction(
            tool_name="pdf_scale_content",
            params={"scale_factor": 0.85},
            target_issues=["table_overflow"],
            reasoning="Scale content down to fit wide tables",
        ),
    ],
    "wrong_orientation": [
        FixAction(
            tool_name="pdf_rotate_pages",
            params={"pages": None, "angle": 90},
            target_issues=["wrong_orientation"],
            reasoning="Rotate pages to correct orientation",
        ),
    ],
    "rgb_colorspace": [
        FixAction(
            tool_name="convert_colorspace",
            params={"target_colorspace": "cmyk"},
            target_issues=["rgb_colorspace"],
            reasoning="Convert RGB images to CMYK for professional print",
        ),
    ],
    "low_dpi_image": [
        FixAction(
            tool_name="check_image_dpi",
            params={"min_dpi": 150},
            target_issues=["low_dpi_image"],
            reasoning="Flag low-DPI images that may print poorly",
        ),
    ],
}

_XLSX_ISSUE_MAP: dict[str, list[FixAction]] = {
    "margin_violation": [
        FixAction(
            tool_name="set_xlsx_margins",
            params={"top": 0.75, "bottom": 0.75, "left": 0.75, "right": 0.75},
            target_issues=["margin_violation"],
            reasoning="Set safe print margins on all sheets",
        ),
    ],
    "text_overflow": [
        FixAction(
            tool_name="set_xlsx_page_setup",
            params={"orientation": "portrait", "paper_size": 1, "fit_to_page": True},
            target_issues=["text_overflow"],
            reasoning="Enable fit-to-page to prevent column overflow",
        ),
    ],
    "table_overflow": [
        FixAction(
            tool_name="auto_fit_xlsx_columns",
            params={"shrink_margins": True},
            target_issues=["table_overflow"],
            reasoning="Auto-fit columns, choose orientation, and enable fit-to-page for wide content",
        ),
    ],
    "wrong_orientation": [
        FixAction(
            tool_name="auto_fit_xlsx_columns",
            params={"shrink_margins": True},
            target_issues=["wrong_orientation"],
            reasoning="Auto-fit columns with smart orientation selection for wide sheets",
        ),
    ],
}

_PPTX_ISSUE_MAP: dict[str, list[FixAction]] = {
    "slide_size_mismatch": [
        FixAction(
            tool_name="set_pptx_slide_size",
            params={"width": 10.0, "height": 7.5},
            target_issues=["slide_size_mismatch"],
            reasoning="Switch to 4:3 standard size for better print compatibility",
        ),
    ],
    "small_font": [
        FixAction(
            tool_name="adjust_pptx_font_size",
            params={"min_size_pt": 10.0},
            target_issues=["small_font"],
            reasoning="Enforce minimum readable font size",
        ),
    ],
    "text_overflow": [
        FixAction(
            tool_name="adjust_pptx_font_size",
            params={"min_size_pt": 10.0},
            target_issues=["text_overflow"],
            reasoning="Adjust font sizes to help prevent text overflow",
        ),
    ],
}

# Page sizes lookup
_PAGE_SIZES: dict[str, tuple[float, float]] = {
    "a4": (8.27, 11.69),
    "letter": (8.5, 11.0),
}


def _severity_passes_filter(severity: IssueSeverity, aggressiveness: str) -> bool:
    """Check if an issue's severity passes the aggressiveness filter."""
    if aggressiveness == "aggressive":
        return True
    if aggressiveness == "moderate":
        return severity in (IssueSeverity.critical, IssueSeverity.warning)
    # conservative
    return severity == IssueSeverity.critical


def _collect_issues(diagnosis: DocumentDiagnosis) -> list[DiagnosisIssue]:
    """Flatten all issues from pages and document-level into a single list."""
    issues: list[DiagnosisIssue] = []
    for page in diagnosis.pages:
        issues.extend(page.issues)
    issues.extend(diagnosis.document_issues)
    return issues


def _is_editable_format(file_type: str) -> bool:
    """Check if a file type supports original-format editing (DOCX tools)."""
    return file_type in (".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp")


def plan_fixes_rule_based(
    diagnosis: DocumentDiagnosis,
    aggressiveness: str,
    file_type: str,
    target_page_size: str | None = None,
    iteration: int = 1,
    failed_issue_types: set[str] | None = None,
) -> FixPlan:
    """
    Deterministic fix planning: map issue types to tool calls with defaults.
    Filters by aggressiveness and deduplicates tool calls.

    When ``failed_issue_types`` is provided and the document is an editable
    format, issues in that set are routed to PDF fallback tools instead of
    the primary DOCX tools.
    """
    is_editable = _is_editable_format(file_type)
    if file_type == ".xlsx":
        issue_map = _XLSX_ISSUE_MAP
    elif file_type == ".pptx":
        issue_map = _PPTX_ISSUE_MAP
    elif is_editable:
        issue_map = _DOCX_ISSUE_MAP
    else:
        issue_map = _PDF_ISSUE_MAP
    failed_issue_types = failed_issue_types or set()
    all_issues = _collect_issues(diagnosis)

    seen_tools: set[str] = set()  # (tool_name, params_key) for dedup
    actions: list[FixAction] = []
    skipped: list[str] = []

    for issue in all_issues:
        issue_type = str(issue.type)

        if not _severity_passes_filter(issue.severity, aggressiveness):
            skipped.append(f"{issue_type} (severity {issue.severity} below threshold)")
            continue

        # If this issue type already failed with DOCX tools, try PDF fallback
        use_fallback = is_editable and issue_type in failed_issue_types and issue_type in _DOCX_TO_PDF_FALLBACK

        if use_fallback:
            active_map = _DOCX_TO_PDF_FALLBACK
            lookup_key = issue_type
        else:
            active_map = issue_map
            # Use suggested_fix from diagnosis if available and in our map
            lookup_key = issue.suggested_fix if issue.suggested_fix in active_map else issue_type

        if lookup_key not in active_map:
            # Last resort: try PDF fallback for editable formats
            if is_editable and issue_type in _DOCX_TO_PDF_FALLBACK:
                active_map = _DOCX_TO_PDF_FALLBACK
                lookup_key = issue_type
            else:
                skipped.append(f"{issue_type} (no fix available)")
                continue

        for template in active_map[lookup_key]:
            action = template.model_copy()

            # Customize params based on context
            if action.tool_name == "set_page_size" and target_page_size:
                w, h = _PAGE_SIZES.get(target_page_size, (8.27, 11.69))
                action.params = {"width": w, "height": h}

            if action.tool_name == "replace_font" and issue.location:
                # Use the font name from the issue location if available
                action.params["from_font"] = issue.location

            # Dedup: skip if we already have this exact tool+params
            dedup_key = f"{action.tool_name}:{json.dumps(action.params, sort_keys=True)}"
            if dedup_key in seen_tools:
                continue
            seen_tools.add(dedup_key)
            actions.append(action)

    # Sort: structural changes first, then content changes;
    # within structural, non-fallback before fallback
    structural_tools = {
        "set_margins",
        "set_page_size",
        "set_orientation",
        "remove_blank_pages",
        "fix_page_breaks",
        "remove_manual_breaks",
        "adjust_paragraph_indents",
        "pdf_crop_margins",
        "pdf_scale_content",
        "pdf_rotate_pages",
        "set_xlsx_margins",
        "set_xlsx_page_setup",
        "auto_fit_xlsx_columns",
        "resize_images_to_fit",
    }
    actions.sort(
        key=lambda a: (
            0 if a.tool_name in structural_tools else 1,
            1 if a.is_fallback else 0,
            a.tool_name,
        )
    )

    return FixPlan(
        job_id=diagnosis.job_id,
        iteration=iteration,
        actions=actions,
        skipped_issues=skipped,
    )


async def plan_fixes_ai(
    diagnosis: DocumentDiagnosis,
    aggressiveness: str,
    file_type: str,
    effort_config: EffortConfig,
    target_page_size: str | None = None,
    iteration: int = 1,
    failed_issue_types: set[str] | None = None,
) -> FixPlan:
    """
    AI-driven fix planning: send diagnosis to Gemini or Claude to get a fix plan.
    Falls back to rule-based if the AI call fails.
    """

    failed_issue_types = failed_issue_types or set()

    # Build fallback context for the prompt
    fallback_context = ""
    if failed_issue_types and _is_editable_format(file_type):
        fallback_context = (
            f"The following issue types failed to resolve "
            f"with {file_type.upper()} tools in previous iterations: "
            f"{', '.join(sorted(failed_issue_types))}. "
            f"You SHOULD use PDF fallback tools for these issues instead. "
            f"Mark these actions with is_fallback=true."
        )

    diagnosis_data = yaml.safe_dump(
        diagnosis.model_dump(mode="json"),
        sort_keys=False,
        indent=2,
        width=1024,
        allow_unicode=True,
        default_flow_style=False,
    )
    system_prompt = get_prompt("fix_planning").render(
        file_type=file_type,
        target_page_size=target_page_size or "original",
        aggressiveness=aggressiveness,
    )

    prompt = f"Plan fixes for the following document diagnosis:\n\n{diagnosis_data}\n\n{fallback_context}"

    try:
        # raw_text = await _call_planning_model(prompt, effort_config)
        raw_text = await _call_gemini(prompt, system_prompt, effort_config)
        findings = PlannerFindings.model_validate_json(raw_text)
        return FixPlan(
            job_id=diagnosis.job_id,
            iteration=iteration,
            actions=findings.actions,
            skipped_issues=[f"{item.type}: {item.reason}" for item in findings.skipped_issues],
        )
        # return _parse_ai_plan(raw_text, diagnosis.job_id, iteration)
    except Exception as e:
        logger.exception(f"Job {diagnosis.job_id}: AI fix planning failed, falling back to rule-based: {e}")
        return plan_fixes_rule_based(
            diagnosis,
            aggressiveness,
            file_type,
            target_page_size,
            iteration,
            failed_issue_types=failed_issue_types,
        )


# async def _call_planning_model(prompt: str, effort_config: EffortConfig) -> str:
#     """Call the appropriate AI model for fix planning."""
#     # Use Claude only if explicitly enabled in settings
#     if settings.USE_ANTHROPIC_AI and effort_config.use_ai_planning and effort_config.orchestration_model is None:
#         return await _call_claude(prompt, effort_config)

#     # Default to Gemini
#     return await _call_gemini(prompt, effort_config)


async def _call_gemini(prompt: str, system_prompt: str, effort_config: EffortConfig) -> str:
    """Call Gemini for fix planning with retry + timeout."""

    model = effort_config.orchestration_model or "gemini-3-flash-preview"

    async def _do_call() -> str:
        resp = await ai_client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config=GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=PlannerFindings.model_json_schema(),
                temperature=1.0,  # Recommended for Gemini 3
                top_p=0.9,
                thinking_config=ThinkingConfig(thinking_level=ThinkingLevel.HIGH),
                system_instruction=system_prompt,
            ),
        )
        return resp.text or ""

    return await with_retry(
        _do_call,
        max_retries=settings.AI_API_MAX_RETRIES,
        retryable=(TimeoutError, asyncio.TimeoutError, ConnectionError, OSError),
        label="gemini-planning",
    )


async def _call_claude(prompt: str, effort_config: EffortConfig) -> str:
    """Call Claude for fix planning (Thorough effort) with retry + timeout."""

    client = get_anthropic_client()
    if not client:
        raise RuntimeError(
            "Anthropic client not configured but USE_ANTHROPIC_AI is True. "
            "Set PFX_ANTHROPIC_API_KEY in .env or disable PFX_USE_ANTHROPIC_AI."
        )

    model = effort_config.claude_model or settings.ANTHROPIC_DIAGNOSIS_MODEL
    logger.debug(f"Calling Claude API with model: {model}")

    async def _do_call() -> str:
        try:
            resp = await asyncio.wait_for(
                asyncio.to_thread(
                    client.messages.create,
                    model=model,
                    max_tokens=4096,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=settings.AI_API_TIMEOUT_SECONDS,
            )
            return extract_anthropic_text(resp)
        except Exception as e:
            logger.error(f"Claude API error: {type(e).__name__}: {e}")
            raise

    return await with_retry(
        _do_call,
        max_retries=settings.AI_API_MAX_RETRIES,
        retryable=(TimeoutError, asyncio.TimeoutError, ConnectionError, OSError),
        label="claude-planning",
    )


# def _parse_ai_plan(raw_text: str, job_id: str, iteration: int) -> FixPlan:
#     """Parse structured JSON from AI response into a FixPlan."""
#     data = json.loads(raw_text)

#     pdf_tools = {"pdf_crop_margins", "pdf_scale_content", "pdf_rotate_pages"}
#     actions: list[FixAction] = []
#     for item in data.get("actions", []):
#         tool = item["tool_name"]
#         actions.append(
#             FixAction(
#                 tool_name=tool,
#                 params=item.get("params", {}),
#                 target_issues=item.get("target_issues", []),
#                 reasoning=item.get("reasoning", ""),
#                 is_fallback=item.get("is_fallback", tool in pdf_tools),
#             )
#         )

#     skipped: list[str] = []
#     for item in data.get("skipped_issues", []):
#         reason = item.get("reason", "")
#         issue_type = item.get("type", "unknown")
#         skipped.append(f"{issue_type}: {reason}")

#     return FixPlan(
#         job_id=job_id,
#         iteration=iteration,
#         actions=actions,
#         skipped_issues=skipped,
#     )


async def plan_fixes(
    diagnosis: DocumentDiagnosis,
    aggressiveness: str,
    file_type: str,
    effort_config: EffortConfig,
    target_page_size: str | None = None,
    iteration: int = 1,
    failed_issue_types: set[str] | None = None,
) -> FixPlan:
    """
    Entry point: choose rule-based or AI planning based on effort + aggressiveness.
    Smart Auto at any effort level triggers AI planning.
    Thorough effort always uses AI planning.

    ``failed_issue_types`` contains issue types whose DOCX fixes failed in
    prior iterations, triggering PDF fallback routing for those issues.
    """
    use_ai = effort_config.use_ai_planning or aggressiveness == "smart_auto"

    if use_ai:
        return await plan_fixes_ai(
            diagnosis,
            aggressiveness,
            file_type,
            effort_config,
            target_page_size,
            iteration,
            failed_issue_types=failed_issue_types,
        )

    return plan_fixes_rule_based(
        diagnosis,
        aggressiveness,
        file_type,
        target_page_size,
        iteration,
        failed_issue_types=failed_issue_types,
    )
