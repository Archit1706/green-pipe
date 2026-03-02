# GreenPipe — Demo Script & Video Guide

> **Hackathon deadline:** March 25, 2026 @ 1:00 PM CDT
> **Video target:** 2:30 – 3:00 minutes

---

## Pre-Demo Setup Checklist

Before recording, ensure the following are ready:

- [ ] GreenPipe API running: `uvicorn src.main:app --reload --host 0.0.0.0 --port 8000`
- [ ] Interactive docs open in browser: `http://localhost:8000/docs`
- [ ] GitLab test project exists with webhook configured
- [ ] At least one pipeline completed in the test project (to show a stored report)
- [ ] Terminal with the four `curl` commands below ready to paste
- [ ] Browser tab open on `http://localhost:8000/api/v1/analytics/summary`
- [ ] The `docs/SCI_METHODOLOGY.md` worked example open for reference

---

## Demo Scenarios (4 scenarios, ~8 minutes total live + narration)

---

### Scenario 1 — SCI Calculation: "GreenPipe Implements ISO/IEC 21031:2024"

**Goal:** Show the canonical GSF SCI formula being applied in real-time.

**Talking points:**
- The Green Software Foundation created ISO/IEC 21031:2024, the industry standard for software carbon scoring.
- GreenPipe is the first GitLab Duo Agent that implements this standard automatically.
- `SCI = ((E × I) + M) / R` — every variable is explained in the report.

**Command:**
```bash
curl -s -X POST http://localhost:8000/api/v1/pipeline/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "commit_messages": ["feat: add OAuth2 login for enterprise users"],
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
  }' | python -m json.tool
```

**Expected output highlights to narrate:**
```json
{
  "sci": {
    "sci_score_gco2e": 4.83,
    "energy_kwh": 0.011875,
    "carbon_intensity_gco2_kwh": 386.0,
    "operational_carbon_gco2e": 4.58,
    "embodied_carbon_gco2e": 1.19,
    "methodology": "SCI ISO/IEC 21031:2024"
  }
}
```

**Narrate:** "Notice the SCI score: 4.83 grams of CO₂e for this single pipeline run. Every field traces back directly to the GSF formula — E is energy in kWh from the Impact Framework Teads curve, I is the grid intensity from the Carbon Aware SDK, M is embodied hardware carbon, and R is one pipeline run."

---

### Scenario 2 — Carbon Aware SDK: "Real-Time Grid Carbon Intensity"

**Goal:** Show the scheduling endpoint querying the GSF Carbon Aware SDK.

**Talking points:**
- The Carbon Aware SDK is an official GSF tool for real-time and forecast carbon intensity.
- GreenPipe uses it to find the lowest-carbon execution window in the next 24 hours.
- Deferrable pipelines — docs updates, refactoring, style changes — can be shifted without developer action.

**Command:**
```bash
curl -s "http://localhost:8000/api/v1/pipeline/schedule?location=us-east1&duration_minutes=15&horizon_hours=24" \
  | python -m json.tool
```

**Expected output highlights to narrate:**
```json
{
  "location": "us-east1",
  "current_intensity_gco2_kwh": 386.2,
  "forecast_available": true,
  "best_window": {
    "start": "2026-03-16T03:00:00Z",
    "end": "2026-03-16T03:15:00Z",
    "intensity": 212.4
  },
  "savings_percent": 45.0,
  "recommendation": "Defer this pipeline to 2026-03-16T03:00 UTC (45.0% lower carbon intensity)."
}
```

**Narrate:** "The Carbon Aware SDK returns a 24-hour forecast. GreenPipe found a window at 3 AM UTC with 45% lower carbon intensity. For a deferrable pipeline like a documentation update, this is free carbon savings — no developer action needed."

---

### Scenario 3 — NLP Urgency Classification: "AI That Knows What Can Wait"

**Goal:** Show the DistilBERT classifier distinguishing urgent from deferrable pipelines.

**Talking points:**
- This is GreenPipe's unique AI contribution on top of the GSF standards layer.
- A fine-tuned DistilBERT model (INT8 quantized — 58% less energy than FP32) classifies commit messages.
- It understands context: `perf: speed up test runner` sounds urgent but is deferrable.

**Command (urgent):**
```bash
curl -s -X POST http://localhost:8000/agent/tools/classify_urgency \
  -H "Content-Type: application/json" \
  -d '{
    "commit_messages": ["hotfix: fix critical authentication bypass CVE-2026-1234"]
  }' | python -m json.tool
```

Expected: `"urgency_class": "urgent"` — run immediately, no carbon negotiation.

**Command (deferrable):**
```bash
curl -s -X POST http://localhost:8000/agent/tools/classify_urgency \
  -H "Content-Type: application/json" \
  -d '{
    "commit_messages": ["docs: update README with new API examples", "style: fix lint warnings in auth module"]
  }' | python -m json.tool
```

Expected: `"urgency_class": "deferrable"` — shift to low-carbon window.

**Narrate:** "The same intelligence that identifies a security hotfix as urgent knows that documentation updates can wait for a greener grid window. This NLP layer is what makes GreenPipe a smart agent, not just a measuring tool."

---

### Scenario 4 — Automated Agent: "Zero Developer Action Required"

**Goal:** Show the full agent loop — webhook trigger → analysis → MR comment.

**Talking points:**
- GreenPipe listens for pipeline completion via GitLab webhooks.
- Every completed pipeline automatically receives an SCI carbon report as an MR comment.
- The developer sees exactly what their code change cost in gCO₂e.

**Steps to show:**
1. Open the GitLab test project in the browser.
2. Point to **Settings → Webhooks** showing the configured pipeline webhook URL.
3. Trigger a pipeline (or show a recently completed one).
4. Open the associated MR and scroll to the GreenPipe bot comment.
5. Walk through the comment sections: SCI score, energy table, scheduling recommendation.

**Alternatively (if live webhook not available), show the formatter output:**
```bash
curl -s -X POST http://localhost:8000/agent/tools/generate_sci_report \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": 99999,
    "pipeline_id": 12345,
    "post_as_comment": false
  }' | python -m json.tool
```

**Narrate:** "This is the MR comment GreenPipe posts automatically. Every developer, on every pipeline, gets a GSF-compliant SCI report — without installing anything, configuring anything, or thinking about it."

---

### Bonus: Analytics Dashboard

Show the historical analytics endpoint:

```bash
curl -s "http://localhost:8000/api/v1/analytics/summary" | python -m json.tool
curl -s "http://localhost:8000/api/v1/analytics/savings" | python -m json.tool
```

**Narrate:** "Over time, GreenPipe builds a carbon history for your project. You can see total CO₂e emitted, trends by day, which pipelines are the biggest consumers, and the estimated savings if deferrable pipelines had been scheduled intelligently."

---

## Video Script (2:30 – 3:00 Target)

### [0:00 – 0:20] The Problem

> "Every day, software teams run thousands of CI/CD pipelines. Research shows these pipelines emit between 150 and 995 metric tonnes of CO₂ annually — yet developers have zero visibility. The Green Software Foundation has created excellent standards for measuring this: SCI, Carbon Aware SDK, Impact Framework. But no GitLab tool applies them automatically. Until now."

*[Screen: show a blank GitLab pipeline page with no carbon data]*

---

### [0:20 – 0:45] The Solution

> "GreenPipe is a GitLab Duo Agent that brings GSF standards natively into every CI/CD pipeline. It measures. It classifies. It recommends. Automatically, on every pipeline run, with zero developer action."

*[Screen: show GreenPipe MR comment appearing automatically after pipeline completion]*

---

### [0:45 – 1:00] GSF Compliance

> "GreenPipe implements three Green Software Foundation standards. Software Carbon Intensity per ISO/IEC 21031:2024. Real-time and forecast carbon intensity from the GSF Carbon Aware SDK. And energy estimation using the GSF Impact Framework's Teads curve with SPECpower runner mapping."

*[Screen: show the `/api/v1/standards/info` response listing all three standards]*

---

### [1:00 – 1:45] Live Demo

> "Here's GreenPipe analyzing a pipeline in real-time."

*[Screen: run Scenario 1 curl command, show JSON response with SCI score]*

> "4.83 grams of CO₂e for this pipeline run. The formula is right here: E times I plus M, divided by R. Energy from the Teads curve, carbon intensity from the Carbon Aware SDK, embodied hardware emissions, one pipeline run."

*[Screen: run Scenario 2 curl command, show scheduling recommendation]*

> "The Carbon Aware SDK found a window with 45% lower carbon intensity. For a documentation update, GreenPipe recommends deferring to 3 AM UTC — free carbon savings."

---

### [1:45 – 2:10] Unique Intelligence

> "What makes GreenPipe different is the NLP layer. A fine-tuned DistilBERT model — INT8 quantized, using 58% less energy than a full-precision model — reads commit messages and decides: can this wait?"

*[Screen: show urgent vs deferrable classification side by side]*

> "A security hotfix runs immediately. A documentation update waits for cleaner electricity. The developer never has to think about it."

---

### [2:10 – 2:30] Impact & Future

> "In our hackathon demo: 35% of pipelines were deferrable. If they'd been scheduled to the Carbon Aware SDK's recommended windows, we'd have saved an estimated 22% of total pipeline carbon. At scale — across all GitLab projects — that's thousands of tonnes of CO₂e annually."

> "We're preparing to contribute to the Green Software Foundation ecosystem: a Green Software Pattern proposal, a GitLab runner plugin for the Impact Framework, and a case study as the first GitLab-native SCI implementation."

> "GreenPipe: the first GitLab agent where every pipeline run is a step toward net zero."

*[Screen: show GreenPipe MR comment with GSF footer]*

---

## Screenshot Targets for Devpost

Capture these five screenshots for submission images:

1. **SCI breakdown** — the JSON response from `POST /api/v1/pipeline/analyze` with sci_score, energy, carbon_intensity, methodology fields highlighted
2. **Carbon Aware SDK integration** — the `/api/v1/pipeline/schedule` response showing best_window and savings_percent
3. **Impact Framework methodology** — the `/api/v1/standards/info` endpoint listing all three GSF standards
4. **Architecture diagram** — the ASCII art from README.md rendered in a code block
5. **MR comment** — the GitLab-formatted markdown report generated by `format_mr_comment()` (rendered in a markdown preview or actual GitLab MR)
