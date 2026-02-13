"""Merge visual and structural diagnosis results into unified output."""

import asyncio

import yaml
from google.genai.types import GenerateContentConfig, ThinkingConfig, ThinkingLevel

from app.core.ai import ai_client
from app.core.config import settings
from app.core.effort import EffortConfig
from app.core.log import logger
from app.core.prompts import get_prompt
from app.core.retry import with_retry
from app.schema.diagnosis import (
    DiagnosisIssue,
    DiagnosisSummary,
    DocumentDiagnosis,
    IssueSeverity,
    IssueSource,
    IssueType,
    MergedPageDiagnosis,
    PageDiagnosis,
)

__all__ = ("merge_diagnoses", "merge_diagnoses_ai")

_SEVERITY_ORDER = {
    IssueSeverity.critical: 0,
    IssueSeverity.warning: 1,
    IssueSeverity.info: 2,
}


def merge_diagnoses(
    visual_pages: list[PageDiagnosis],
    structural_issues: list[DiagnosisIssue],
    job_id: str,
    effort_level: str,
    file_type: str,
    page_count: int,
) -> DocumentDiagnosis:
    """Rule-based merge for Quick and Standard effort levels."""
    # Collect all visual issues by page
    page_issues: dict[int, list[DiagnosisIssue]] = {}
    for vp in visual_pages:
        page_issues.setdefault(vp.page, []).extend(vp.issues)

    # Split structural issues into page-level and document-level
    doc_issues: list[DiagnosisIssue] = []
    for si in structural_issues:
        if si.page is not None:
            page_issues.setdefault(si.page, []).append(si)
        else:
            doc_issues.append(si)

    # Deduplicate per page
    merged_pages: list[PageDiagnosis] = []
    for page_num in sorted(page_issues.keys()):
        deduped = _deduplicate_issues(page_issues[page_num])
        merged_pages = [*merged_pages, PageDiagnosis(page=page_num, issues=deduped)]

    # Include pages with no issues (from visual scan)
    seen_pages = {p.page for p in merged_pages}
    for vp in visual_pages:
        if vp.page not in seen_pages:
            merged_pages = [*merged_pages, PageDiagnosis(page=vp.page, issues=[])]
    merged_pages.sort(key=lambda p: p.page)

    # Compute summary
    all_issues = [i for p in merged_pages for i in p.issues] + doc_issues
    summary = _compute_summary(all_issues)

    return DocumentDiagnosis(
        job_id=job_id,
        effort_level=effort_level,
        file_type=file_type,
        page_count=page_count,
        pages=merged_pages,
        document_issues=doc_issues,
        summary=summary,
    )


async def merge_diagnoses_ai(
    visual_pages: list[PageDiagnosis],
    structural_issues: list[DiagnosisIssue],
    job_id: str,
    effort_level: str,
    file_type: str,
    page_count: int,
    config: EffortConfig,
) -> DocumentDiagnosis:
    """AI-assisted merge using Gemini (or Claude if enabled) for Thorough effort level."""
    # Serialize findings for the prompt
    # visual_json = json.dumps([p.model_dump() for p in visual_pages], indent=2)
    # structural_json = json.dumps([i.model_dump() for i in structural_issues], indent=2)

    visual_findings = yaml.safe_dump(
        [p.model_dump(mode="json") for p in visual_pages],
        sort_keys=False,
        indent=2,
        width=1024,
        allow_unicode=True,
        default_flow_style=False,
    )

    structural_findings = yaml.safe_dump(
        [i.model_dump(mode="json") for i in structural_issues],
        sort_keys=False,
        indent=2,
        width=1024,
        allow_unicode=True,
        default_flow_style=False,
    )

    prompt = "Merge findings from visual inspection and structural review into a unified diagnosis of print quality issues.\n\n"

    system_prompt = get_prompt("merge_diagnosis").render(
        total_pages=page_count,
        file_type=file_type,
        visual_findings=visual_findings,
        structural_findings=structural_findings,
    )

    try:
        raw_text = await _call_gemini_merge(prompt, system_prompt, config, job_id)
        findings = MergedPageDiagnosis.model_validate_json(raw_text)  # type: ignore
        return DocumentDiagnosis(
            job_id=job_id,
            effort_level=effort_level,
            file_type=file_type,
            page_count=page_count,
            pages=findings.pages,
            document_issues=findings.document_issues,
            summary=_compute_summary([i for p in findings.pages for i in p.issues] + findings.document_issues),
        )

        # data = json.loads(raw_text)
        # return _parse_ai_merge_response(
        #     data,
        #     job_id,
        #     effort_level,
        #     file_type,
        #     page_count,
        # )
    except Exception:
        logger.exception(f"Job {job_id}: AI merge failed, falling back to rule-based merge")
        return merge_diagnoses(
            visual_pages,
            structural_issues,
            job_id,
            effort_level,
            file_type,
            page_count,
        )


async def _call_gemini_merge(
    prompt: str,
    system_prompt: str,
    config: EffortConfig,
    job_id: str,
) -> str:
    """Call Gemini for diagnosis merge."""

    model = config.visual_model or "gemini-3-flash-preview"

    async def _do_call() -> str:
        resp = await ai_client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config=GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=MergedPageDiagnosis.model_json_schema(),
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
        label=f"gemini-merge({job_id})",
    )


def _deduplicate_issues(issues: list[DiagnosisIssue]) -> list[DiagnosisIssue]:
    """Deduplicate issues by type + location overlap."""
    if not issues:
        return issues

    seen: dict[tuple[str, str | None], DiagnosisIssue] = {}
    for issue in issues:
        key = (issue.type, issue.location)
        if key in seen:
            existing = seen[key]
            # Keep the one with higher confidence
            winner = issue if issue.confidence > existing.confidence else existing
            # Mark as merged if sources differ
            if issue.source != existing.source:
                seen[key] = DiagnosisIssue(
                    type=winner.type,
                    severity=min(
                        issue.severity,
                        existing.severity,
                        key=lambda s: _SEVERITY_ORDER[s],
                    ),
                    source=IssueSource.merged,
                    page=winner.page,
                    location=winner.location,
                    description=winner.description,
                    suggested_fix=winner.suggested_fix,
                    confidence=max(issue.confidence, existing.confidence),
                )
            else:
                seen[key] = winner
        else:
            seen[key] = issue

    # Sort by severity then confidence
    result = list(seen.values())
    result.sort(key=lambda i: (_SEVERITY_ORDER[i.severity], -i.confidence))
    return result


def _compute_summary(all_issues: list[DiagnosisIssue]) -> DiagnosisSummary:
    """Compute aggregate summary statistics."""
    total = len(all_issues)
    critical = sum(1 for i in all_issues if i.severity == IssueSeverity.critical)
    warning = sum(1 for i in all_issues if i.severity == IssueSeverity.warning)
    info = sum(1 for i in all_issues if i.severity == IssueSeverity.info)

    if critical > 0:
        readiness = "major_issues"
    elif warning > 0:
        readiness = "needs_fixes"
    else:
        readiness = "ready"

    sorted_issues = sorted(
        all_issues,
        key=lambda i: (_SEVERITY_ORDER[i.severity], -i.confidence),
    )
    top_issues = [i.description for i in sorted_issues[:5]]

    return DiagnosisSummary(
        total_issues=total,
        critical_count=critical,
        warning_count=warning,
        info_count=info,
        top_issues=top_issues,
        print_readiness=readiness,
    )


def _parse_ai_merge_response(
    data: dict,
    job_id: str,
    effort_level: str,
    file_type: str,
    page_count: int,
) -> DocumentDiagnosis:
    """Parse Claude's merge response into a DocumentDiagnosis."""
    pages: list[PageDiagnosis] = []
    for page_data in data.get("pages", []):
        issues = []
        for issue_data in page_data.get("issues", []):
            issue = _safe_parse_issue(issue_data, page_data.get("page"))
            if issue:
                issues.append(issue)
        pages.append(
            PageDiagnosis(
                page=page_data.get("page", 0),
                issues=issues,
            )
        )

    doc_issues = []
    for issue_data in data.get("document_issues", []):
        issue = _safe_parse_issue(issue_data, None)
        if issue:
            doc_issues.append(issue)

    all_issues = [i for p in pages for i in p.issues] + doc_issues
    summary = _compute_summary(all_issues)

    return DocumentDiagnosis(
        job_id=job_id,
        effort_level=effort_level,
        file_type=file_type,
        page_count=page_count,
        pages=pages,
        document_issues=doc_issues,
        summary=summary,
    )


def _safe_parse_issue(data: dict, page: int | None) -> DiagnosisIssue | None:
    """Parse an issue from AI response with validation."""
    try:
        return DiagnosisIssue(
            type=IssueType(data["type"]),
            severity=IssueSeverity(data.get("severity", "warning")),
            source=IssueSource(data.get("source", "merged")),
            page=data.get("page", page),
            location=data.get("location"),
            description=data.get("description", "Issue detected"),
            suggested_fix=data.get("suggested_fix"),
            confidence=max(0.0, min(1.0, float(data.get("confidence", 0.7)))),
        )
    except ValueError, KeyError:
        return None
