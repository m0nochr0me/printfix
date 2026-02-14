# PrintFix 🖨️✨

**AI-powered document repair for perfect prints.**

![Python](https://img.shields.io/badge/Python-3.14+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)
![MCP](https://img.shields.io/badge/MCP-Enabled-orange)
![License](https://img.shields.io/badge/License-MIT-red)

PrintFix is a dual-stack **REST API** and **MCP Server** that makes documents (PDF, DOCX, XLSX, PPTX) print-ready by detecting and fixing layout issues (margins, page breaks, font sizes, tables, images, etc.).

It combines programmatic tools with multimodal AI (Google Gemini + Claude) to orchestrate an intelligent repair loop—diagnosing issues, applying fixes, and verifying the results visually.

## ✨ Features

- **Multi-Format Ingestion**: Supports `.docx`, `.xlsx`, `.pptx`, `.pdf`, and images.
- **AI Diagnostics**: Visual and structural analysis using Gemini 3.0 Flash/Pro.
- **Smart Orchestration**: An AI agent plans and executes fixes until the document converges to a print-ready state.
- **MCP Integration**: Exposes all fix capabilities as Model Context Protocol tools.
- **Verification**: Automated before/after visual comparison with confidence scoring.
- **Robust Pipeline**: Async workers (Taskiq) + Redis for reliable job processing.

## 🚀 Quick Start

### Prerequisites

- Python 3.14+
- [uv](https://github.com/astral-sh/uv) (recommended)
- Docker (optional)
- Redis

### Environment Setup

Create a `.env` file with your keys:

```bash
PFX_APP_AUTH_KEY=your-secret-key
PFX_GOOGLE_API_KEY=your-gemini-key
```

### Run Locally

```bash
# Install dependencies
uv sync

# Start the server (API + Web UI + MCP)
python -m app

# Start the worker (in a separate terminal)
taskiq worker app.worker.broker:broker --fs-discover --tasks-pattern "app/**/tasks.py"
```

### Run with Docker

```bash
docker build -t printfix .
docker run -p 8083:8083 --env-file .env printfix
```

## 🛠️ Usage

Use WEB UI at `http://localhost:8083` or interact programmatically via the REST API.

Submit a document for repair:

```bash
curl -X POST "http://localhost:8083/v1/jobs" \
  -H "Authorization: Bearer $PFX_APP_AUTH_KEY" \
  -F "file=@presentation.pptx" \
  -F "effort=standard"
```

Check the status:

```bash
curl "http://localhost:8083/v1/jobs/{job_id}" \
  -H "Authorization: Bearer $PFX_APP_AUTH_KEY"
```

See [USAGE.md](USAGE.md) for full API documentation.

## 🏗️ Architecture

PrintFix operates as a hybrid server:

- **FastAPI**: Serves the REST API, Web UI, and health checks.
- **FastMCP**: Exposes the `printfix` MCP toolset.
- **Worker**: Dedicated Taskiq process for heavy lifting (rendering, AI analysis).

## 🗺️ Roadmap

- [x] Core Ingestion & Rendering
- [x] Visual & Structural Diagnosis
- [x] Fix Orchestration Loop
- [x] Verification & confident scoring
- [ ] Advanced Color Management
- [ ] User Feedback Learning Hook
