# PrintFix — Implementation Plan

A SaaS REST API that makes customer documents print-ready by automatically detecting and fixing common layout/formatting issues using a combination of programmatic tools and multimodal AI.

## Design Decisions

1. **AI Models:** Both Gemini and Claude. User selects an "effort" level that maps to model combinations (e.g. fast/cheap vs thorough/expensive).
2. **Fix strategy:** Fix the original document (DOCX, XLSX, PPTX) when possible. Fall back to direct PDF manipulation when the original format is unsupported or fixes fail.
3. **Aggressiveness:** Configurable per job. Includes a "Smart Auto" mode where the AI agent decides fix aggressiveness based on the document's state.
4. **Deployment:** Online-only SaaS. SOTA model access required; large documents may need high-RAM servers.

---

## 1. High-Level Flow

```
Upload  →  Ingest & Normalize  →  AI Diagnosis  →  Fix Loop  →  Verify  →  Deliver
                                       ↑               |
                                       └── re-diagnose ┘
```

Each job progresses through a state machine:

```
uploaded → ingesting → diagnosing → fixing → verifying → done
                                                      ↘ needs_review (if confidence < threshold)
```

---

## 2. Effort Levels

| Level | Diagnosis Model | Orchestration Model | Passes | Use Case |
|-------|----------------|---------------------|--------|----------|
| **Quick** | Gemini Flash | Gemini Flash | 1 | Simple fixes: margins, page size, orientation |
| **Standard** | Gemini Pro | Gemini Pro | up to 3 | Most jobs: tables, fonts, page breaks, images |
| **Thorough** | Gemini Pro (visual) + Claude Opus (structural) | Claude Opus | up to 5 | Complex layouts, multi-section documents, tricky edge cases |

The effort level controls: which models are called, how many diagnosis-fix-verify iterations run, and how aggressive the AI is allowed to be.

---

## 3. File Ingestion Layer

### Supported Input Formats
- PDF (.pdf)
- Microsoft Word (.docx)
- Microsoft Excel (.xlsx)
- Microsoft PowerPoint (.pptx)
- OpenDocument (.odt, .ods, .odp)
- Images (.jpg, .png, .tiff) — treated as single-page print jobs

### Ingestion Pipeline
1. Accept upload via REST API (multipart form)
2. Validate file type and size limits
3. Store original file (S3-compatible object storage or local disk)
4. Extract metadata:
   - Page count, page sizes, orientation
   - Margins (from document structure, not visual)
   - Fonts used (and whether embedded)
   - Embedded image count, resolution, color space
   - Table/cell structure (for DOCX/XLSX)
5. Render a reference PDF via LibreOffice headless (for non-PDF inputs)
6. Render each page to PNG via `pdf2image` for AI visual inspection

### Libraries
- `python-docx` — DOCX parsing and editing
- `openpyxl` — XLSX parsing and editing
- `python-pptx` — PPTX parsing and editing
- `pikepdf` / `pypdf` — PDF parsing and low-level editing
- `pdf2image` + Pillow — page-to-image rendering
- LibreOffice headless — universal format conversion and rendering

---

## 4. AI Diagnosis Engine

Two complementary analysis passes that feed into a unified issue list.

### Pass A — Visual Inspection (Multimodal)

Send rendered page images to the AI model with a structured prompt requesting JSON output.

**Detected issues:**
- Content clipped at page edges
- Text too close to margins (not enough bleed/safe area)
- Orphan/widow lines
- Misaligned elements (headers, columns, images)
- Images crossing page boundaries
- Text overflow / overlapping elements
- Unreadably small font sizes
- Wrong page orientation for content
- Blank or near-blank pages (accidental page breaks)
- Visual inconsistencies (mixed fonts, erratic spacing)

**Output per page:**
```json
{
  "page": 3,
  "issues": [
    {
      "type": "clipped_content",
      "severity": "high",
      "location": "right edge, rows 2-5 of table",
      "description": "Table extends beyond printable area, rightmost column is cut off",
      "suggested_fix": "auto_fit_tables"
    }
  ]
}
```

### Pass B — Structural Inspection (Programmatic)

Parse the document structure directly. Catches issues invisible to visual inspection:

- Non-embedded fonts (will render differently on print server)
- RGB color space (should be CMYK for professional print)
- Low-resolution images (< 150 DPI at print size)
- Missing or incorrect page size metadata
- Inconsistent margin definitions across sections
- Soft vs hard page breaks in wrong places
- Table cell widths exceeding page width
- Hidden content / tracked changes left in

### Unified Diagnosis

Merge visual and structural findings. Deduplicate. Assign final severity (critical / warning / info). Prioritize by impact on print quality.

---

## 5. Fix Orchestration via MCP Tool Server

The AI agent acts as the conductor. It reads the diagnosis, selects appropriate tools, applies fixes, and verifies results. Each fix is exposed as an MCP tool.

### MCP Tools

**Page & Layout**
- `set_page_size(doc, width, height)` — resize to target paper (A4, Letter, etc.)
- `set_margins(doc, top, bottom, left, right)` — adjust document margins
- `set_orientation(doc, orientation)` — portrait / landscape
- `remove_blank_pages(doc)` — delete accidental empty pages

**Text & Typography**
- `replace_font(doc, from_font, to_font)` — substitute missing/problematic fonts
- `embed_fonts(doc)` — embed all used fonts into the document
- `adjust_font_size(doc, selector, new_size)` — resize text in a section/style
- `fix_orphans_widows(doc, strategy)` — adjust spacing to eliminate orphans/widows

**Tables**
- `auto_fit_tables(doc)` — shrink columns to fit page width
- `resize_table_text(doc, table_index, max_font_size)` — reduce text in overflowing cells
- `split_wide_table(doc, table_index)` — break a too-wide table across pages

**Images**
- `convert_colorspace(doc, target)` — RGB to CMYK conversion
- `check_image_dpi(doc, min_dpi)` — flag or upscale low-res images
- `resize_image_to_fit(doc, image_index)` — scale image within page bounds

**Page Breaks**
- `fix_page_breaks(doc, strategy)` — reflow content with smarter break points
- `remove_manual_breaks(doc)` — strip hard page breaks, let content reflow

**PDF-Specific Fallbacks**
- `pdf_crop_margins(pdf, margins)` — adjust PDF crop/media boxes
- `pdf_scale_content(pdf, scale_factor)` — scale all content to fit
- `pdf_rotate_pages(pdf, pages, angle)` — rotate specific pages
- `pdf_overlay_fix(pdf, page, overlay)` — patch a page with corrected content

### Fix Loop

```
for iteration in range(max_iterations):       # controlled by effort level
    diagnosis = diagnose(doc)
    if diagnosis.is_clean or diagnosis.only_info:
        break
    fixes = ai_agent.select_fixes(diagnosis)   # AI picks tools + params
    for fix in fixes:
        apply(fix)
    doc = re_render(doc)                        # fresh render for next pass
```

### Smart Auto Mode

When aggressiveness is set to "Smart Auto", the AI agent receives the full diagnosis and decides per-issue:
- **Conservative** for content-affecting changes (font substitution, table splitting)
- **Aggressive** for safe structural changes (margins, page size, orientation, blank page removal)
- **Skip** for issues below a severity threshold or where the fix risks making things worse
- The agent explains its reasoning per decision in the job log

---

## 6. Verification & Confidence Scoring

After the fix loop completes:

1. Re-render final document to page images
2. AI performs a final visual comparison (before vs after)
3. Compute a confidence score per page and overall:
   - 90-100: print-ready, no issues detected
   - 70-89: likely fine, minor concerns
   - 50-69: some issues remain, human review recommended
   - < 50: significant problems, manual intervention needed
4. Generate a before/after diff (side-by-side page images)
5. Produce a human-readable fix report (what was changed and why)

**Threshold behavior:**
- Above threshold → auto-approve, mark as `done`
- Below threshold → mark as `needs_review`, notify shop employee

---

## 7. REST API (FastAPI)

### Endpoints

```
POST   /jobs                        Upload file, set effort level & aggressiveness
GET    /jobs/{id}                   Job status, diagnosis summary, confidence score
GET    /jobs/{id}/diagnosis         Full diagnosis detail (all issues found)
GET    /jobs/{id}/fixes             List of fixes applied with explanations
GET    /jobs/{id}/preview           Before/after page image URLs
GET    /jobs/{id}/preview/{page}    Single page before/after
POST   /jobs/{id}/approve           Accept result, finalize
POST   /jobs/{id}/reject            Flag for manual review
GET    /jobs/{id}/download          Download fixed file (original format or PDF)
DELETE /jobs/{id}                   Delete job and all associated files

GET    /health                      Service health check
```

### Request: Create Job

```json
POST /jobs
Content-Type: multipart/form-data

file: <binary>
effort: "quick" | "standard" | "thorough"
aggressiveness: "conservative" | "moderate" | "aggressive" | "smart_auto"
target_page_size: "A4" | "letter" | "original"    (optional)
target_colorspace: "cmyk" | "rgb" | "original"     (optional)
```

### Response: Job Status

```json
{
  "id": "job_abc123",
  "status": "done",
  "effort": "standard",
  "original_filename": "invoice.docx",
  "pages": 4,
  "issues_found": 6,
  "issues_fixed": 5,
  "issues_skipped": 1,
  "confidence": 92,
  "created_at": "2026-02-08T12:00:00Z",
  "completed_at": "2026-02-08T12:00:47Z"
}
```

---

## 8. Caching & Job Queue (Redis)

- **Job state machine** — track each job's progress through states
- **Processing queue** — async worker pool picks up jobs; controls concurrency
- **Diagnosis cache** — keyed by file content hash + effort level; skip re-diagnosis for identical re-uploads
- **Rate limiting** — per API key / per tenant

---

## 9. Project Structure

```
printfix/
├── main.py                     FastAPI app entrypoint
├── pyproject.toml
├── api/
│   ├── routes.py               Endpoint definitions
│   ├── models.py               Pydantic request/response schemas
│   └── dependencies.py         Auth, rate limiting, shared deps
├── core/
│   ├── ingestion.py            File upload, metadata extraction, rendering
│   ├── diagnosis.py            AI visual + structural analysis
│   ├── orchestrator.py         Fix loop, AI agent logic
│   ├── verification.py         Final check, confidence scoring
│   └── models.py               Internal domain models (Job, Issue, Fix)
├── fixes/
│   ├── mcp_server.py           MCP tool server exposing all fix tools
│   ├── page_layout.py          Page size, margins, orientation fixes
│   ├── typography.py           Font replacement, sizing, orphans/widows
│   ├── tables.py               Table auto-fit, splitting, text resize
│   ├── images.py               Colorspace, DPI, resizing
│   ├── page_breaks.py          Page break fixes
│   └── pdf_fallback.py         Direct PDF manipulation fallbacks
├── ai/
│   ├── gemini.py               Gemini API client (Flash, Pro)
│   ├── claude.py               Claude API client (Opus)
│   ├── prompts.py              All AI prompt templates
│   └── effort.py               Effort level → model routing config
├── workers/
│   ├── queue.py                Redis job queue management
│   └── processor.py            Background worker: runs the full pipeline
├── storage/
│   └── files.py                File storage abstraction (local / S3)
└── config.py                   Settings, env vars, thresholds
```

---

## 10. Implementation Order

### Phase 1 — Foundation
- [ ] FastAPI app skeleton with job CRUD endpoints
- [ ] File upload + storage
- [ ] Redis job queue + state machine
- [ ] LibreOffice headless integration (format conversion + PDF rendering)
- [ ] Page-to-image rendering pipeline

### Phase 2 — Diagnosis
- [ ] Gemini integration for visual page analysis
- [ ] Structural analysis for DOCX (margins, fonts, tables, page breaks)
- [ ] Structural analysis for PDF
- [ ] Unified diagnosis model (merge visual + structural)
- [ ] Effort level routing (model selection)

### Phase 3 — Core Fixes
- [ ] MCP tool server setup
- [ ] Page layout fixes (margins, page size, orientation)
- [ ] Font fixes (substitution, embedding)
- [ ] Table fixes (auto-fit, text resize)
- [ ] Page break fixes

### Phase 4 — Orchestration
- [ ] AI agent fix loop (diagnose → fix → re-render → re-diagnose)
- [ ] Smart Auto aggressiveness mode
- [ ] Claude integration for thorough-effort orchestration
- [ ] Iteration cap and convergence detection

### Phase 5 — Verification & Delivery
- [ ] Before/after rendering and comparison
- [ ] AI confidence scoring
- [ ] Fix report generation
- [ ] Download endpoint (fixed original + PDF)

### Phase 6 — PDF Fallbacks
- [ ] PDF margin/crop adjustments
- [ ] PDF content scaling
- [ ] PDF page rotation
- [ ] Fallback routing when original-format fixes fail

### Phase 7 — Hardening
- [ ] Image colorspace conversion (RGB → CMYK)
- [ ] Image DPI detection and handling
- [ ] XLSX and PPTX structural analysis + fixes
- [ ] Error handling, retries, timeouts
- [ ] API authentication and rate limiting
- [ ] Logging and observability
