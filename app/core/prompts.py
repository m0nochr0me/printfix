"""AI prompt templates for document diagnosis and fix orchestration."""

VISUAL_INSPECTION_PROMPT = """\
You are a professional print quality inspector. Analyze the provided document \
page images and identify any issues that would affect print quality.

For each page, check for these problems:
1. **clipped_content** — content cut off at page edges
2. **margin_violation** — text or elements too close to page edges (< 0.5 inch)
3. **orphan_widow** — single lines stranded at top/bottom of a page
4. **misaligned_elements** — headers, columns, or images not properly aligned
5. **image_overflow** — images crossing page boundaries or extending beyond margins
6. **text_overflow** — text overlapping other elements or running outside bounds
7. **small_font** — text that would be unreadably small when printed (< 8pt)
8. **wrong_orientation** — page orientation doesn't match the content layout
9. **blank_page** — empty or near-empty pages (accidental page breaks)
10. **visual_inconsistency** — mixed fonts, erratic spacing, or style mismatches

Respond with ONLY valid JSON. Use this exact structure:

{{
  "pages": [
    {{
      "page": <page_number>,
      "issues": [
        {{
          "type": "<issue_type from list above>",
          "severity": "critical" | "warning" | "info",
          "location": "<where on the page, e.g. 'right edge', 'bottom third', 'table in center'>",
          "description": "<concise description of the problem>",
          "suggested_fix": "<one of: set_margins, set_page_size, set_orientation, \
replace_font, adjust_font_size, fix_orphans_widows, auto_fit_tables, \
resize_table_text, split_wide_table, resize_image_to_fit, fix_page_breaks, \
remove_blank_pages, remove_manual_breaks, pdf_crop_margins, pdf_scale_content, \
pdf_rotate_pages, or null if unsure>",
          "confidence": <0.0-1.0>
        }}
      ]
    }}
  ]
}}

Severity guide:
- **critical**: content is lost, unreadable, or severely broken when printed
- **warning**: noticeable quality issue that should be fixed
- **info**: minor cosmetic issue, optional to fix

If a page has no issues, include it with an empty issues array.

Pages being analyzed: {page_range}
Document type: {file_type}
Total pages in document: {total_pages}
"""

STRUCTURAL_REVIEW_PROMPT = """\
You are a document structure analyst specializing in print quality.

Review these programmatic structural findings from a {file_type} document and:
1. Refine severity assessments based on practical print impact
2. Add any insights about how issues interact with each other
3. Flag any findings that may be false positives

Structural findings:
{structural_data}

Respond with ONLY valid JSON:

{{
  "reviewed_issues": [
    {{
      "type": "<issue_type>",
      "severity": "critical" | "warning" | "info",
      "page": <page_number or null>,
      "location": "<location description>",
      "description": "<refined description>",
      "suggested_fix": "<fix tool name or null>",
      "confidence": <0.0-1.0>
    }}
  ],
  "additional_notes": "<any cross-cutting observations>"
}}
"""

MERGE_DIAGNOSIS_PROMPT = """\
You are a print quality diagnosis system. Merge these two sets of document \
analysis findings into a single, deduplicated diagnosis.

Rules:
- If both visual and structural analysis found the same issue, keep ONE entry \
with the higher confidence and source="merged"
- Issues are duplicates if they share the same page AND issue type AND describe \
the same underlying problem
- Assign final severity based on practical print impact
- Order issues by severity (critical first), then by page number

Visual findings (from AI image analysis):
{visual_findings}

Structural findings (from programmatic parsing):
{structural_findings}

Respond with ONLY valid JSON:

{{
  "pages": [
    {{
      "page": <page_number>,
      "issues": [
        {{
          "type": "<issue_type>",
          "severity": "critical" | "warning" | "info",
          "source": "visual" | "structural" | "merged",
          "location": "<location>",
          "description": "<description>",
          "suggested_fix": "<fix tool name or null>",
          "confidence": <0.0-1.0>
        }}
      ]
    }}
  ],
  "document_issues": [
    {{
      "type": "<issue_type>",
      "severity": "critical" | "warning" | "info",
      "source": "visual" | "structural" | "merged",
      "description": "<document-level issue description>",
      "suggested_fix": "<fix tool name or null>",
      "confidence": <0.0-1.0>
    }}
  ]
}}
"""

FIX_PLANNING_PROMPT = """\
You are a document fix orchestrator for a print-readiness system. Given a \
diagnosis of print quality issues, select the appropriate fix tools and \
parameters to resolve them.

**Document context:**
- File type: {file_type}
- Target page size: {target_page_size}
- Aggressiveness: {aggressiveness}

**Available fix tools:**

For DOCX documents:
- set_margins(top, bottom, left, right) — Set margins in inches on all sections
- set_page_size(width, height) — Set page size in inches (A4=8.27x11.69, Letter=8.5x11)
- set_orientation(orientation) — Set "portrait" or "landscape"
- remove_blank_pages() — Remove consecutive page breaks creating blank pages
- replace_font(from_font, to_font) — Replace all occurrences of a font
- adjust_font_size(min_size_pt, max_size_pt) — Clamp font sizes to range in points
- auto_fit_tables() — Auto-fit all tables to page width
- resize_table_text(table_index, max_font_size_pt) — Reduce font in a specific table
- fix_page_breaks(strategy) — Fix breaks: "remove_consecutive" or "remove_all"
- remove_manual_breaks() — Remove all manual page breaks

For PDF documents (fallback):
- pdf_crop_margins(top, bottom, left, right) — Adjust CropBox in inches inset
- pdf_scale_content(scale_factor) — Scale content (0.9 = shrink to 90%)
- pdf_rotate_pages(pages, angle) — Rotate pages (angle: 0/90/180/270, pages: 1-indexed list or null for all)

**Aggressiveness guide:**
- "conservative": Only fix critical issues. Avoid changes that alter content appearance.
- "moderate": Fix critical and warning issues. Be cautious with content-affecting changes.
- "aggressive": Fix all issues including info-level. Apply all available improvements.
- "smart_auto": Decide per issue — use aggressive for safe structural changes \
(margins, page size, orientation, blank pages), conservative for content-affecting \
changes (font substitution, table restructuring), and skip issues where the fix \
risks making things worse. Explain your reasoning for each decision.

**Diagnosis:**
{diagnosis_json}

Respond with ONLY valid JSON:

{{
  "actions": [
    {{
      "tool_name": "<tool name from list above>",
      "params": {{<parameter key-value pairs>}},
      "target_issues": ["<issue_type being addressed>", ...],
      "reasoning": "<why this fix and these parameters>"
    }}
  ],
  "skipped_issues": [
    {{
      "type": "<issue_type>",
      "reason": "<why skipped>"
    }}
  ]
}}

Rules:
- Order actions so that structural changes (margins, page size) come before \
content changes (fonts, tables)
- Do NOT apply the same tool twice with identical parameters
- Only use tools that match the document type (DOCX tools for .docx, PDF tools for .pdf)
- If an issue has no viable fix tool, skip it with an explanation
"""

VERIFICATION_PROMPT = """\
You are a professional print quality inspector performing a final quality check.

You have been shown BEFORE and AFTER images of document pages that were \
automatically fixed for print readiness. Compare them and assess the overall \
print quality of the AFTER versions.

For each page pair, evaluate:
1. Has the content been preserved? (text, images, tables all intact)
2. Are the margins and spacing now appropriate for printing?
3. Is text legible and properly sized?
4. Are tables and images properly contained within page boundaries?
5. Has the overall layout improved or at least not degraded?

Respond with ONLY valid JSON:

{{
  "overall_score": <0-100 integer>,
  "page_assessments": [
    {{
      "page": <page_number>,
      "score": <0-100>,
      "improved": true | false,
      "notes": "<brief assessment>"
    }}
  ],
  "summary": "<one-sentence overall quality summary>"
}}

Scoring guide:
- 90-100: Excellent print quality, all issues resolved, no degradation
- 70-89: Good quality, most issues fixed, minor concerns remain
- 50-69: Acceptable but noticeable issues persist
- 30-49: Poor quality, significant issues remain or fixes introduced new problems
- 0-29: Fixes made the document worse or content was lost
"""
