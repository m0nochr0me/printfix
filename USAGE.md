# PrintFix API Usage Examples

All requests require a Bearer token set via the `PFX_APP_AUTH_KEY` environment variable.

```bash
# Set your token (must match PFX_APP_AUTH_KEY in .env)
TOKEN="your-secret-token"
BASE="http://localhost:8083"
```

---

## 1. Submit a Document

Upload a file and start the full pipeline (ingest → diagnose → fix → verify).

```bash
# Basic — defaults to standard effort, smart_auto aggressiveness
curl -X POST "$BASE/v1/jobs" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@./my-document.docx"

# With options
curl -X POST "$BASE/v1/jobs" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@./report.pdf" \
  -F "effort=thorough" \
  -F "aggressiveness=aggressive" \
  -F "target_page_size=a4" \
  -F "target_colorspace=cmyk"
```

**Parameters:**

| Field              | Values                                                                             | Default      |
|--------------------|------------------------------------------------------------------------------------|--------------|
| `file`             | `.pdf`, `.docx`, `.xlsx`, `.pptx`, `.odt`, `.ods`, `.odp`, `.jpg`, `.png`, `.tiff` | *(required)* |
| `effort`           | `quick`, `standard`, `thorough`                                                    | `standard`   |
| `aggressiveness`   | `conservative`, `moderate`, `aggressive`, `smart_auto`                             | `smart_auto` |
| `target_page_size` | `a4`, `letter`, `original`                                                         | *(none)*     |
| `target_colorspace`| `cmyk`, `rgb`, `original`                                                          | *(none)*     |

**Response** (202 Accepted):

```json
{
  "id": "01J5XYZABC...",
  "status": "uploaded",
  "original_filename": "report.pdf",
  "created_at": "2026-02-09T12:00:00Z"
}
```

Save the `id` — you'll use it for all subsequent calls.

```bash
JOB_ID="01J5XYZABC..."
```

---

## 2. Poll Job Status

The job moves through states automatically:
`uploaded` → `ingesting` → `converting` → `rendering` → `ingested` → `diagnosing` → `diagnosed` → `fixing` → `verifying` → `done`

```bash
curl -s "$BASE/v1/jobs/$JOB_ID" \
  -H "Authorization: Bearer $TOKEN" | jq .
```

**Response:**

```json
{
  "id": "01J5XYZABC...",
  "status": "done",
  "effort": "standard",
  "aggressiveness": "smart_auto",
  "original_filename": "report.pdf",
  "file_type": ".pdf",
  "pages": 12,
  "issues_found": 5,
  "issues_fixed": 4,
  "confidence": 87.5,
  "print_readiness": "good"
}
```

**Poll until complete:**

```bash
while true; do
  STATUS=$(curl -s "$BASE/v1/jobs/$JOB_ID" \
    -H "Authorization: Bearer $TOKEN" | jq -r .status)
  echo "Status: $STATUS"
  [[ "$STATUS" == "done" || "$STATUS" == "needs_review" || "$STATUS" == "failed" ]] && break
  sleep 3
done
```

---

## 3. View Diagnosis

See what issues were detected.

```bash
curl -s "$BASE/v1/jobs/$JOB_ID/diagnosis" \
  -H "Authorization: Bearer $TOKEN" | jq .
```

---

## 4. View Applied Fixes

See the list of fixes that were applied.

```bash
curl -s "$BASE/v1/jobs/$JOB_ID/fixes" \
  -H "Authorization: Bearer $TOKEN" | jq .
```

---

## 5. View Orchestration Result

See the fix loop summary (iterations, convergence, fallback usage).

```bash
curl -s "$BASE/v1/jobs/$JOB_ID/orchestration" \
  -H "Authorization: Bearer $TOKEN" | jq .
```

---

## 6. View Verification & Report

```bash
# Full verification result (confidence scores, page comparisons)
curl -s "$BASE/v1/jobs/$JOB_ID/verification" \
  -H "Authorization: Bearer $TOKEN" | jq .

# Human-readable fix report
curl -s "$BASE/v1/jobs/$JOB_ID/report" \
  -H "Authorization: Bearer $TOKEN" | jq -r .report
```

---

## 7. Preview Pages

```bash
# List all rendered page image paths
curl -s "$BASE/v1/jobs/$JOB_ID/preview" \
  -H "Authorization: Bearer $TOKEN" | jq .

# Download a specific page image (page 1)
curl -s "$BASE/v1/jobs/$JOB_ID/preview/1" \
  -H "Authorization: Bearer $TOKEN" -o page1.png

# Before/after comparison metadata
curl -s "$BASE/v1/jobs/$JOB_ID/preview/comparison" \
  -H "Authorization: Bearer $TOKEN" | jq .

# Download before (pre-fix) page image
curl -s "$BASE/v1/jobs/$JOB_ID/preview/before/1" \
  -H "Authorization: Bearer $TOKEN" -o before_page1.png

# Download after (post-fix) page image
curl -s "$BASE/v1/jobs/$JOB_ID/preview/after/1" \
  -H "Authorization: Bearer $TOKEN" -o after_page1.png
```

---

## 8. Download Fixed File

```bash
# Download as PDF (default)
curl -s "$BASE/v1/jobs/$JOB_ID/download" \
  -H "Authorization: Bearer $TOKEN" -o fixed_report.pdf

# Download in original format (e.g., DOCX)
curl -s "$BASE/v1/jobs/$JOB_ID/download?format=original" \
  -H "Authorization: Bearer $TOKEN" -o fixed_report.docx
```

---

## 9. Approve / Reject

If a job lands in `needs_review` (low confidence), you can approve or reject it.

```bash
# Approve — marks job as done
curl -X POST "$BASE/v1/jobs/$JOB_ID/approve" \
  -H "Authorization: Bearer $TOKEN"

# Reject — keeps job in needs_review for re-processing
curl -X POST "$BASE/v1/jobs/$JOB_ID/reject" \
  -H "Authorization: Bearer $TOKEN"
```

---

## 10. Re-trigger Diagnosis or Fixing

```bash
# Re-run diagnosis (must be in ingested or diagnosed state)
curl -X POST "$BASE/v1/jobs/$JOB_ID/diagnose" \
  -H "Authorization: Bearer $TOKEN"

# Re-run fix orchestration (must be in diagnosed or needs_review state)
curl -X POST "$BASE/v1/jobs/$JOB_ID/fix" \
  -H "Authorization: Bearer $TOKEN"
```

---

## 11. Delete a Job

```bash
curl -X DELETE "$BASE/v1/jobs/$JOB_ID" \
  -H "Authorization: Bearer $TOKEN"
```

---

## Full End-to-End Example

```bash
TOKEN="your-secret-token"
BASE="http://localhost:8083"

# 1. Upload
JOB_ID=$(curl -s -X POST "$BASE/v1/jobs" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@./invoice.docx" \
  -F "effort=standard" | jq -r .id)
echo "Job created: $JOB_ID"

# 2. Wait for completion
while true; do
  STATUS=$(curl -s "$BASE/v1/jobs/$JOB_ID" \
    -H "Authorization: Bearer $TOKEN" | jq -r .status)
  echo "  Status: $STATUS"
  [[ "$STATUS" == "done" || "$STATUS" == "needs_review" || "$STATUS" == "failed" ]] && break
  sleep 5
done

# 3. Check results
echo "=== Diagnosis ==="
curl -s "$BASE/v1/jobs/$JOB_ID/diagnosis" \
  -H "Authorization: Bearer $TOKEN" | jq '.diagnosis.summary'

echo "=== Fixes ==="
curl -s "$BASE/v1/jobs/$JOB_ID/fixes" \
  -H "Authorization: Bearer $TOKEN" | jq '.fixes[] | {tool: .tool_name, success, description}'

echo "=== Report ==="
curl -s "$BASE/v1/jobs/$JOB_ID/report" \
  -H "Authorization: Bearer $TOKEN" | jq -r .report

# 4. Download
curl -s "$BASE/v1/jobs/$JOB_ID/download" \
  -H "Authorization: Bearer $TOKEN" -o invoice_fixed.pdf
echo "Downloaded: invoice_fixed.pdf"
```

---

## Health Check

```bash
curl -s "$BASE/health" | jq .
```

No authentication required.
