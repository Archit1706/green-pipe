# GreenPipe — Carbon-Aware CI/CD Agent

> **First GitLab Duo Agent implementing Green Software Foundation standards**

## Description

GreenPipe is an intelligent GitLab Duo Agent that brings **Green Software Foundation (GSF)
standards** natively into CI/CD pipelines. It automatically measures the carbon footprint of
every pipeline run, classifies urgency using AI, and recommends carbon-aware scheduling to
reduce emissions.

### Value Proposition

- **NOT just a calculator** — an always-on agent that monitors every pipeline automatically
- **NOT reinventing** — built on proven GSF standards (SCI, Carbon Aware SDK, Impact Framework)
- **IS innovating** — first GitLab-native automation + NLP intelligence layer on top of GSF standards

---

## Standards Implemented

| Standard | Version | Role |
| -------- | ------- | ---- |
| **Software Carbon Intensity (SCI)** | ISO/IEC 21031:2024 | `SCI = ((E × I) + M) / R` — canonical formula for software carbon scoring |
| **GSF Carbon Aware SDK** | latest | Real-time and forecast grid carbon intensity data |
| **GSF Impact Framework — Teads Curve** | latest | CPU utilization → power factor mapping for energy estimation |
| **ECO-CI SPECpower approach** | research | Runner hardware TDP mapping via SPECpower benchmarks |

---

## Tools

### `analyze_pipeline`

Analyzes a completed CI/CD pipeline using GSF SCI methodology.

**Endpoint:** `POST /agent/tools/analyze_pipeline`

**Input:**
```json
{
  "project_id": 12345,
  "pipeline_id": 67890,
  "runner_location": "us-east1"
}
```

**Output:** Full GSF-compliant analysis report including SCI score, energy breakdown,
carbon intensity, and scheduling recommendation.

---

### `generate_sci_report`

Generates a formatted markdown SCI report and optionally posts it as a GitLab MR comment.

**Endpoint:** `POST /agent/tools/generate_sci_report`

**Input:**
```json
{
  "project_id": 12345,
  "pipeline_id": 67890,
  "post_as_comment": true,
  "mr_iid": 42
}
```

**Output:** Formatted markdown report with SCI breakdown, energy data, and
carbon-aware recommendations. When `post_as_comment` is `true`, the report is
posted directly to the merge request.

---

### `suggest_scheduling`

Recommends optimal pipeline execution windows based on carbon intensity forecasts.

**Endpoint:** `POST /agent/tools/suggest_scheduling`

**Input:**
```json
{
  "location": "us-east1",
  "duration_minutes": 15,
  "horizon_hours": 24
}
```

**Output:** Best execution window with carbon intensity forecast and estimated
savings percentage.

---

### `classify_urgency`

Uses AI to classify commit message urgency. Determines whether a pipeline can be
safely deferred to a lower-carbon window.

**Endpoint:** `POST /agent/tools/classify_urgency`

**Input:**
```json
{
  "commit_messages": ["hotfix: fix critical auth bypass", "docs: update README"],
  "pipeline_id": 67890,
  "project_id": 12345
}
```

**Output:** Urgency class (`urgent` / `normal` / `deferrable`) with confidence score
and a plain-language explanation.

---

### `analyze_code_efficiency`

Analyses MR code for energy efficiency using Anthropic Claude (Green Code Profiler).

**Endpoint:** `POST /agent/tools/analyze_code_efficiency`

**Input:**
```json
{
  "project_id": 12345,
  "mr_iid": 42,
  "diff_text": null
}
```

Provide `project_id` + `mr_iid` to fetch the diff from GitLab, **or** `diff_text` for
offline analysis.

**Output:** Structured list of energy-efficiency suggestions with issue type,
line range, estimated energy impact (low/medium/high), and actionable fix.

**Requires:** `ANTHROPIC_API_KEY` environment variable.

**Architecture note:** GreenPipe uses a hybrid AI approach — a tiny INT8 DistilBERT
for frequent urgency classification, and Claude for on-demand deep code analysis.
This ensures the agent itself practises sustainable design.

---

## Triggers

### Pipeline Completion

GreenPipe automatically triggers on every pipeline completion event.

**Webhook URL:** `POST /agent/webhooks/pipeline`

Configure in **GitLab → Settings → Webhooks** with:
- Trigger: **Pipeline events**
- Secret token: value of `GITLAB_WEBHOOK_SECRET`

When triggered, GreenPipe:

1. Checks pipeline status is terminal (`success`, `failed`, `canceled`)
2. Fetches job data and commit messages from GitLab API
3. Estimates energy using GSF Impact Framework Teads Curve
4. Queries carbon intensity from GSF Carbon Aware SDK
5. Calculates SCI per ISO/IEC 21031:2024
6. Classifies urgency via DistilBERT NLP (INT8 quantized)
7. **Evaluates auto-deferral decision** based on policy mode:
   - `recommend-only` (default): posts report with savings recommendation
   - `approval-required`: posts report with `@greenpipe confirm-defer` prompt
   - `auto-execute`: cancels the pipeline and creates a schedule for the optimal window
8. Posts a sustainability report (with deferral action) as an MR comment

### `@greenpipe` Mention

Responds to `@greenpipe` mentions in merge request comments.

**Webhook URL:** `POST /agent/webhooks/mention`

Configure in **GitLab → Settings → Webhooks** with:
- Trigger: **Comments**
- Secret token: value of `GITLAB_WEBHOOK_SECRET`

Supported commands (case-insensitive):

| Command | Description |
| ------- | ----------- |
| `@greenpipe analyze` | Analyze the latest pipeline for this MR |
| `@greenpipe report` | Generate a full GSF SCI report (same as analyze) |
| `@greenpipe schedule` | Show carbon-optimal execution windows |
| `@greenpipe optimize` | Analyse MR code for energy efficiency (Claude AI) |
| `@greenpipe defer` | Cancel the pipeline and reschedule to the best low-carbon window |
| `@greenpipe run-now` | Override deferral — retry the pipeline immediately |
| `@greenpipe confirm-defer` | Approve a pending deferral (approval-required mode) |
| `@greenpipe why` | Explain the urgency classification decision |
| `@greenpipe help` | List available commands |

---

## Context

GreenPipe accesses:

- Pipeline job data (duration, runner type, CPU utilization)
- Commit history (for NLP urgency classification)
- Runner specification metadata (for energy estimation via SPECpower)
- Real-time and forecast carbon intensity data (via GSF Carbon Aware SDK)
- Historical pipeline analytics (PostgreSQL — optional)

---

## Intelligence

| Capability | Technology | What Makes It Unique |
| ---------- | ---------- | -------------------- |
| **NLP urgency classification** | DistilBERT fine-tuned on 256 commit examples + keyword fallback | Distinguishes `hotfix:` emergencies from `docs:` deferrals automatically |
| **INT8 quantized inference** | PyTorch dynamic quantization | ~60% energy reduction vs. FP32, aligned with GSF Sustainable Design criteria |
| **Carbon-aware scheduling** | GSF Carbon Aware SDK 24-hour forecasts | Finds lowest-carbon window in next 24 hours for deferrable pipelines |
| **Green code profiling** | Anthropic Claude (on-demand) | Deep energy-efficiency analysis of MR diffs — N+1 queries, missing caching, sync I/O |
| **Standards-based scoring** | ISO/IEC 21031:2024 SCI formula | Vendor-neutral, auditable carbon metric every developer can understand |

---

## Auto-Deferral Policy

GreenPipe can autonomously reschedule deferrable pipelines to lower-carbon windows.

| Setting | Default | Description |
| ------- | ------- | ----------- |
| `GREENPIPE_DEFER_MODE` | `recommend-only` | `recommend-only` / `approval-required` / `auto-execute` |
| `GREENPIPE_MIN_SAVINGS_PCT` | `20.0` | Minimum carbon savings (%) to trigger action |
| `GREENPIPE_MAX_DELAY_HOURS` | `24` | Maximum hours a pipeline can be deferred |
| `GREENPIPE_PROTECTED_BRANCHES` | `main,master,release*` | Never defer these branches (glob patterns) |
| `GREENPIPE_PROTECTED_ENVS` | `production,staging` | Never defer these environments |

Every deferral decision is logged to the `deferral_audit_log` table with full context:
original intensity, target window, predicted savings, urgency class, and action taken.

---

## Configuration

Set these environment variables (see `.env.example`):

```env
# Required for live GitLab API access
GITLAB_TOKEN=<personal-access-token>      # Scopes: api, ai_features

# Required for webhook authentication
GITLAB_WEBHOOK_SECRET=<random-secret>     # Must match the GitLab webhook secret token

# Optional: Anthropic Claude API for code efficiency analysis
ANTHROPIC_API_KEY=<your-anthropic-api-key>

# Optional: self-hosted Carbon Aware SDK endpoint
CARBON_AWARE_SDK_URL=http://localhost:5073

# Optional: PostgreSQL for historical analytics
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/greenpipe
```

---

## One-Click Installation

Add GreenPipe to any GitLab project with a single `include:` line in your `.gitlab-ci.yml`:

```yaml
include:
  - project: 'archit1706/green-pipe'
    ref: main
    file: 'templates/greenpipe-ci.yml'
```

Then set these **CI/CD Variables** in your project (Settings → CI/CD → Variables):

| Variable | Required | Description |
| -------- | -------- | ----------- |
| `GREENPIPE_API_URL` | Yes | Base URL of your GreenPipe instance |
| `GITLAB_WEBHOOK_SECRET` | Yes | Must match GreenPipe's `GITLAB_WEBHOOK_SECRET` |
| `GREENPIPE_DEFER_MODE` | No | `recommend-only` (default) / `approval-required` / `auto-execute` |

That's it! GreenPipe will automatically analyse every pipeline and post carbon reports to your MRs.

### GitLab Duo Agent Platform

GreenPipe is registered as a GitLab Duo Agent via `.gitlab/agents/greenpipe/config.yaml`.
This enables discovery through GitLab's agent platform and provides structured tool definitions.

---

## API Quick Reference

| Method | Path | Description |
| ------ | ---- | ----------- |
| `POST` | `/api/v1/pipeline/analyze` | Offline or live pipeline analysis |
| `GET`  | `/api/v1/pipeline/{id}/report` | Fetch stored report |
| `GET`  | `/api/v1/pipeline/{id}/sci` | Fetch stored SCI breakdown |
| `GET`  | `/api/v1/standards/info` | List implemented GSF standards |
| `GET`  | `/api/v1/health` | Health check |
| `POST` | `/agent/tools/analyze_pipeline` | Agent tool: analyze pipeline |
| `POST` | `/agent/tools/generate_sci_report` | Agent tool: generate + post report |
| `POST` | `/agent/tools/suggest_scheduling` | Agent tool: find best window |
| `POST` | `/agent/tools/classify_urgency` | Agent tool: NLP urgency classification |
| `POST` | `/agent/tools/analyze_code_efficiency` | Agent tool: Claude code profiler |
| `POST` | `/agent/webhooks/pipeline` | Pipeline completion webhook |
| `POST` | `/agent/webhooks/mention` | @greenpipe mention webhook |

---

## Attribution

Built on **Green Software Foundation** standards:

- Energy calculations: [GSF Impact Framework](https://if.greensoftware.foundation/)
- Carbon intensity: [GSF Carbon Aware SDK](https://github.com/Green-Software-Foundation/carbon-aware-sdk)
- SCI scoring: [ISO/IEC 21031:2024](https://sci.greensoftware.foundation/)
- Runner mapping: [ECO-CI SPECpower approach](https://www.green-coding.io/products/eco-ci/)

---

*GreenPipe — the first GitLab-native automation layer for Green Software Foundation standards.*
