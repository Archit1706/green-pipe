# GreenPipe

**Built on Green Software Foundation Standards**

> The first GitLab Duo Agent implementing GSF standards (SCI, Carbon Aware SDK,
> Impact Framework) with AI-powered urgency classification and carbon-aware scheduling.

[![Tests](https://img.shields.io/badge/tests-123%20passing-brightgreen)](tests/)
[![GSF SCI](https://img.shields.io/badge/GSF-SCI%20ISO%2FIEC%2021031%3A2024-blue)](https://sci.greensoftware.foundation/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What GreenPipe Does

GreenPipe is a **GitLab Duo Agent** that:

1. **Measures** the carbon footprint of every CI/CD pipeline using GSF SCI methodology
2. **Classifies** pipeline urgency from commit messages via fine-tuned DistilBERT NLP
3. **Recommends** carbon-optimal scheduling windows using the GSF Carbon Aware SDK
4. **Reports** automatically as GitLab MR comments after every pipeline completion

---

## GSF Standards Implemented

| Standard | Version | Role |
| -------- | ------- | ---- |
| **Software Carbon Intensity (SCI)** | ISO/IEC 21031:2024 | `SCI = ((E × I) + M) / R` — canonical carbon formula |
| **GSF Carbon Aware SDK** | latest | Real-time and forecast grid carbon intensity |
| **GSF Impact Framework — Teads Curve** | latest | CPU utilization → energy estimation |
| **ECO-CI SPECpower approach** | research | Runner hardware TDP mapping |

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  GitLab Duo Agent (GreenPipe)                            │
│  - Pipeline completion webhook  → auto-analyse + report  │
│  - @greenpipe mention handler   → on-demand commands     │
│  - DistilBERT NLP classifier    → urgency classification │
└──────────────────────────────────────────────────────────┘
                        ↓ calls
┌──────────────────────────────────────────────────────────┐
│  FastAPI Backend                                         │
│  - Pipeline analyzer orchestrator                        │
│  - SCI calculator  (ISO/IEC 21031:2024)                  │
│  - Energy estimator (GSF Impact Framework Teads curve)   │
│  - Carbon service  (GSF Carbon Aware SDK)                │
│  - Analytics engine (historical CO₂e trends)             │
└──────────────────────────────────────────────────────────┘
                        ↓ uses
┌──────────────────────────────────────────────────────────┐
│  GSF Standards & Tools                                   │
│  - Carbon Aware SDK  (real-time carbon intensity API)    │
│  - SCI Spec          (ISO/IEC 21031:2024)                │
│  - Impact Framework  (Teads curve energy methodology)    │
│  - ECO-CI approach   (SPECpower runner TDP mapping)      │
└──────────────────────────────────────────────────────────┘
```

---

## Quick Start

```bash
# 1. Clone
git clone <repo-url> green-pipe && cd green-pipe

# 2. Install
pip install -e ".[dev]"

# 3. Configure
cp .env.example .env
# Fill in GITLAB_TOKEN, DATABASE_URL, CARBON_AWARE_SDK_URL

# 4. (Optional) Start PostgreSQL
docker-compose up db -d
alembic upgrade head

# 5. Run the API server
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# 6. Tests
pytest tests/ -v
# → 123 passed
```

Interactive API docs: `http://localhost:8000/docs`

---

## Agent Setup (GitLab Webhooks)

Configure two webhooks in **GitLab → Settings → Webhooks**:

| Trigger | URL | Secret |
| ------- | --- | ------ |
| Pipeline events | `https://your-host/agent/webhooks/pipeline` | `GITLAB_WEBHOOK_SECRET` |
| Comments | `https://your-host/agent/webhooks/mention` | `GITLAB_WEBHOOK_SECRET` |

Once configured, GreenPipe will:
- Auto-post an SCI carbon report on every completed pipeline
- Respond to `@greenpipe analyze`, `@greenpipe schedule`, `@greenpipe help` in MR comments

See [`AGENTS.md`](AGENTS.md) for the full agent specification.

---

## API Reference

### Pipeline Analysis

| Method | Path | Description |
| ------ | ---- | ----------- |
| `POST` | `/api/v1/pipeline/analyze` | Analyze pipeline (offline or live GitLab) |
| `GET`  | `/api/v1/pipeline/{id}/report` | Fetch stored report |
| `GET`  | `/api/v1/pipeline/{id}/sci` | Fetch SCI breakdown |
| `GET`  | `/api/v1/pipeline/schedule` | Find carbon-optimal execution window |
| `GET`  | `/api/v1/standards/info` | List implemented GSF standards |
| `GET`  | `/api/v1/health` | Health check |

### Analytics (Historical)

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET`  | `/api/v1/analytics/summary` | Aggregate CO₂e and SCI stats |
| `GET`  | `/api/v1/analytics/trends` | SCI trend grouped by day (up to 365 days) |
| `GET`  | `/api/v1/analytics/top-consumers` | Highest-carbon pipeline runs |
| `GET`  | `/api/v1/analytics/savings` | Estimated CO₂e savings from deferral |

### Agent Tools

| Method | Path | Description |
| ------ | ---- | ----------- |
| `POST` | `/agent/tools/analyze_pipeline` | Analyze a pipeline (structured JSON output) |
| `POST` | `/agent/tools/generate_sci_report` | Generate + optionally post MR comment |
| `POST` | `/agent/tools/suggest_scheduling` | Best low-carbon execution window |
| `POST` | `/agent/tools/classify_urgency` | NLP urgency classification |

### Webhooks

| Method | Path | Trigger |
| ------ | ---- | ------- |
| `POST` | `/agent/webhooks/pipeline` | GitLab pipeline completion event |
| `POST` | `/agent/webhooks/mention` | `@greenpipe` mention in MR comment |

---

## Example: Offline Pipeline Analysis

```bash
curl -X POST http://localhost:8000/api/v1/pipeline/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "commit_messages": ["feat: add OAuth2 login"],
    "runner_location": "us-east1",
    "jobs": [
      {
        "job_name": "build",
        "runner_type": "saas-linux-medium-amd64",
        "duration_seconds": 300,
        "cpu_utilization_percent": 60
      },
      {
        "job_name": "test",
        "runner_type": "saas-linux-medium-amd64",
        "duration_seconds": 600,
        "cpu_utilization_percent": 45
      }
    ]
  }'
```

Response (abbreviated):

```json
{
  "sci": {
    "sci_score_gco2e": 4.83,
    "energy_kwh": 0.011875,
    "carbon_intensity_gco2_kwh": 386.0,
    "methodology": "SCI ISO/IEC 21031:2024"
  },
  "scheduling": {
    "urgency_class": "normal",
    "can_defer": false,
    "message": "Pipeline classified as normal — proceed as scheduled."
  }
}
```

---

## How It Works

### Energy Estimation (GSF Impact Framework)

```
energy_kWh = (TDP_watts × teads_factor(cpu_util%) × duration_s) / 3 600 000
```

**Teads curve** (CPU utilization % → TDP factor):

| CPU% | 0 | 10 | 50 | 100 |
| ---- | - | -- | -- | --- |
| Factor | 0.12 | 0.32 | 0.75 | 1.02 |

**Runner TDP values** (SPECpower-based):

| Runner | TDP (W) |
| ------ | ------- |
| saas-linux-small-amd64 | 65 |
| saas-linux-medium-amd64 | 95 |
| saas-linux-large-amd64 | 125 |
| saas-linux-xlarge-amd64 | 165 |

### SCI Calculation (ISO/IEC 21031:2024)

```
SCI = ((E × I) + M) / R

E = energy_kWh               (from Teads curve above)
I = carbon_intensity gCO₂/kWh  (from GSF Carbon Aware SDK)
M = embodied carbon gCO₂     (amortised hardware lifecycle / E × 100 proxy)
R = 1 pipeline_run           (functional unit)
```

### NLP Urgency Classification

Fine-tuned DistilBERT (INT8 quantized, 58% less energy than FP32):

| Class | Signals | Action |
| ----- | ------- | ------ |
| `urgent` | hotfix, critical, security, cve | Run immediately |
| `normal` | feat, fix, ci, build | Run on schedule |
| `deferrable` | docs, refactor, style, chore | Shift to low-carbon window |

Keyword-based fallback activates automatically when the model directory
(`models/urgency_classifier/`) is absent.

---

## Training the NLP Model (optional)

The service works out-of-the-box with the keyword fallback.  To train the
full DistilBERT model:

```bash
# Install ML dependencies
pip install transformers torch scikit-learn

# Train (256 labeled examples included)
python -m src.nlp.trainer \
  --data data/commit_messages.csv \
  --output models/urgency_classifier

# Quantize to INT8 (58% energy reduction)
python -m src.nlp.quantize \
  --model models/urgency_classifier
```

Target metrics: ≥75% macro F1 after 5 epochs.

---

## Project Structure

```
green-pipe/
├── AGENTS.md                   # GitLab Duo Agent specification
├── SUBMISSION.md               # Devpost submission text (copy-paste ready)
├── LICENSE                     # MIT License
├── src/
│   ├── main.py                 # FastAPI app + lifespan
│   ├── config.py               # pydantic-settings
│   ├── database.py             # async SQLAlchemy (lazy engine)
│   ├── models/pipeline.py      # ORM: PipelineRun, PipelineJob, GSFComplianceLog
│   ├── estimators/
│   │   └── energy_estimator.py # GSF Impact Framework (Teads curve + SPECpower)
│   ├── calculators/
│   │   └── sci_calculator.py   # ISO/IEC 21031:2024 SCI formula
│   ├── services/
│   │   ├── carbon_service.py   # GSF Carbon Aware SDK + regional fallback
│   │   ├── gitlab_client.py    # python-gitlab wrapper (lazy import)
│   │   └── pipeline_analyzer.py # Orchestrator
│   ├── nlp/
│   │   ├── classifier.py       # UrgencyClassifier (INT8 + keyword fallback)
│   │   ├── trainer.py          # DistilBERT fine-tuning
│   │   ├── dataset.py          # CommitMessageDataset (PyTorch)
│   │   └── quantize.py         # INT8 dynamic quantization
│   └── api/
│       ├── routes.py           # Core pipeline endpoints
│       ├── agent_routes.py     # Agent tools + webhooks
│       ├── analytics_routes.py # Historical analytics + schedule
│       ├── report_formatter.py # GitLab MR markdown comment generator
│       ├── schemas.py          # Core Pydantic schemas
│       ├── agent_schemas.py    # Agent tool schemas
│       └── analytics_schemas.py # Analytics response schemas
├── tests/                      # 123 tests, zero external dependencies
├── data/
│   └── commit_messages.csv     # 256 labeled training examples
├── docs/
│   ├── GSF_ALIGNMENT.md        # GSF standards compliance
│   ├── SCI_METHODOLOGY.md      # Step-by-step SCI worked example
│   ├── SUSTAINABLE_DESIGN.md   # GreenPipe's own carbon footprint
│   ├── GSF_CONTRIBUTION.md     # Proposed GSF pattern / IF plugin draft
│   ├── DEMO_SCRIPT.md          # Demo scenarios + video script
│   └── COMPLIANCE_CHECKLIST.md # Final GSF compliance review
├── alembic/                    # DB migrations
├── docker-compose.yml          # PostgreSQL + API
└── Dockerfile
```

---

## Environment Variables

```env
# GitLab API
GITLAB_URL=https://gitlab.com
GITLAB_TOKEN=<personal-access-token>  # Scopes: api, ai_features
GITLAB_WEBHOOK_SECRET=<random-secret> # Must match GitLab webhook secret token

# Database (optional — fallback to in-memory-only mode)
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/greenpipe

# GSF Carbon Aware SDK
CARBON_AWARE_SDK_URL=http://localhost:5073

# Application
APP_ENV=development   # Set to "production" to disable auto-table creation
LOG_LEVEL=INFO
```

---

## Sustainable Design

GreenPipe applies the same optimisations it recommends to its users:

- **INT8 quantized NLP model** — 58% less energy than FP32 DistilBERT
- **1-hour carbon intensity cache** — 97% reduction in Carbon Aware SDK calls
- **Keyword fallback** — zero ML inference cost when model absent
- **Async I/O** — single process handles concurrent requests efficiently
- **Lazy imports** — ML model not loaded until first inference request

See [`docs/SUSTAINABLE_DESIGN.md`](docs/SUSTAINABLE_DESIGN.md) for full details including
GreenPipe's own SCI score (~0.00079 gCO₂e per pipeline analysis).

---

## Attribution

Built on **Green Software Foundation** standards:

- **Carbon Aware SDK** — [github.com/Green-Software-Foundation/carbon-aware-sdk](https://github.com/Green-Software-Foundation/carbon-aware-sdk)
- **Impact Framework** — [if.greensoftware.foundation](https://if.greensoftware.foundation/)
- **SCI Specification** — [sci.greensoftware.foundation](https://sci.greensoftware.foundation/) (ISO/IEC 21031:2024)
- **ECO-CI / Green Coding Berlin** — [green-coding.io/products/eco-ci](https://www.green-coding.io/products/eco-ci/) (SPECpower mapping)

## License

MIT
