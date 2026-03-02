# GreenPipe

**Built on Green Software Foundation Standards**

> The first GitLab Duo Agent implementing GSF standards (SCI, Carbon Aware SDK, Impact Framework) with AI-powered urgency classification and carbon-aware scheduling.

---

## Standards Implemented

| Standard | Version | Role |
|---|---|---|
| Software Carbon Intensity (SCI) | ISO/IEC 21031:2024 | Carbon scoring formula: `SCI = ((E × I) + M) / R` |
| GSF Carbon Aware SDK | latest | Real-time and forecast grid carbon intensity |
| GSF Impact Framework – Teads Curve | latest | CPU utilization → energy estimation |
| ECO-CI SPECpower approach | research | Runner hardware TDP mapping |

## Architecture

```
┌─────────────────────────────────────────────┐
│  GitLab Duo Agent (GreenPipe)               │
│  - Pipeline completion trigger              │
│  - NLP urgency classifier (DistilBERT)      │
│  - Scheduling optimizer                     │
└─────────────────────────────────────────────┘
            ↓ calls
┌─────────────────────────────────────────────┐
│  FastAPI Backend Service                    │
│  - Pipeline analyzer orchestrator           │
│  - SCI calculator (GSF spec)                │
│  - Energy estimator (Impact Framework)      │
│  - Carbon service (Carbon Aware SDK)        │
└─────────────────────────────────────────────┘
            ↓ uses
┌─────────────────────────────────────────────┐
│  GSF Standards & Tools                      │
│  - Carbon Aware SDK (carbon intensity API)  │
│  - SCI Spec (ISO/IEC 21031:2024)            │
│  - Impact Framework (Teads curve)           │
│  - ECO-CI approach (SPECpower mapping)      │
└─────────────────────────────────────────────┘
```

## Project Structure

```
green-pipe/
├── src/
│   ├── main.py                    # FastAPI application
│   ├── config.py                  # Settings (pydantic-settings)
│   ├── database.py                # SQLAlchemy async engine
│   ├── models/
│   │   └── pipeline.py            # ORM models (PipelineRun, PipelineJob, GSFComplianceLog)
│   ├── estimators/
│   │   └── energy_estimator.py    # GSF Teads curve + SPECpower energy estimation
│   ├── calculators/
│   │   └── sci_calculator.py      # SCI per ISO/IEC 21031:2024
│   ├── services/
│   │   └── carbon_service.py      # Carbon Aware SDK integration
│   └── api/
│       ├── schemas.py             # Pydantic request/response models
│       └── routes.py              # FastAPI route handlers
├── tests/
│   ├── test_energy_estimator.py
│   └── test_sci_calculator.py
├── docs/
│   └── GSF_ALIGNMENT.md
├── pyproject.toml
└── .env.example
```

## Quick Start

```bash
# 1. Clone and enter the directory
git clone <repo-url> green-pipe && cd green-pipe

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -e ".[dev]"

# 4. Copy and configure environment
cp .env.example .env
# Edit .env with your GitLab token, DB URL, and Carbon Aware SDK endpoint

# 5. Run the API server
uvicorn src.main:app --reload

# 6. Run tests
pytest tests/ -v
```

The API will be available at `http://localhost:8000`.
Interactive docs: `http://localhost:8000/docs`

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/pipeline/analyze` | Analyze pipeline using GSF standards |
| `GET` | `/api/v1/pipeline/{id}/report` | Get stored sustainability report |
| `GET` | `/api/v1/pipeline/{id}/sci` | Get SCI breakdown |
| `GET` | `/api/v1/standards/info` | List implemented GSF standards |
| `GET` | `/api/v1/health` | Health check |

### Example: Analyze a pipeline

```bash
curl -X POST http://localhost:8000/api/v1/pipeline/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "gitlab_pipeline_id": 12345,
    "project_id": 678,
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
    ],
    "commit_messages": ["feat: add user authentication"]
  }'
```

## How It Works

### Energy Estimation (GSF Impact Framework)

Uses the Teads curve methodology from the GSF Impact Framework:

```
avg_power = runner_TDP × teads_factor(cpu_utilization)
energy_kWh = (avg_power × duration_seconds) / 3_600_000
```

Teads curve breakpoints (CPU utilization % → TDP factor):
- 0% → 0.12, 10% → 0.32, 50% → 0.75, 100% → 1.02

### SCI Calculation (ISO/IEC 21031:2024)

```
SCI = ((E × I) + M) / R

E = energy_kWh              (from Teads curve)
I = carbon_intensity_gCO2/kWh  (from Carbon Aware SDK)
M = embodied_carbon_gCO2    (amortised from hardware lifecycle)
R = 1 pipeline_run          (functional unit)
```

### Carbon Intensity (GSF Carbon Aware SDK)

Queries the Carbon Aware SDK REST API for real-time and forecast grid
carbon intensity. Falls back to regional averages when the SDK is
unavailable.

### NLP Urgency Classification (Week 3)

DistilBERT fine-tuned on commit messages to classify pipelines as:
- **urgent** — hotfix, critical security patches (run immediately)
- **normal** — features, bug fixes (run normally)
- **deferrable** — docs, refactors, style changes (can shift to low-carbon window)

## Attribution

This project builds on the excellent work of the Green Software Foundation.

- Carbon intensity data: [GSF Carbon Aware SDK](https://github.com/Green-Software-Foundation/carbon-aware-sdk)
- Energy methodology: [GSF Impact Framework](https://if.greensoftware.foundation/) (Teads curve)
- Carbon scoring: [SCI Specification](https://sci.greensoftware.foundation/) (ISO/IEC 21031:2024)
- Runner mapping: [ECO-CI](https://www.green-coding.io/products/eco-ci/) (SPECpower approach)

## License

MIT
