"""AI prompt templates for document diagnosis."""

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
