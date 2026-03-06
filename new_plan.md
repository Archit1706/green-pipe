# GreenPipe — Post-Week-6 Feature Plan

> **Context:** All 10 brainstormed ideas have been evaluated against the current codebase (123/123 tests, deadline March 25). This file captures what to build, why, how it integrates, and in what order — with no code written yet.

---

## Strategic Assessment First

### The One Critical Gap

The hackathon rules require the agent to **perform a specific action or workflow automation — not just a text-based chat or report**. Right now, GreenPipe analyzes brilliantly and recommends intelligently, but it never *does* anything autonomously. The pipeline webhook fires → we post a comment that says "consider deferring this" → pipeline keeps running. That's a gap judges will notice.

This is the highest-priority fix: the agent must demonstrably **take action**.

### Prize Category Map

| Prize | Amount | What Unlocks It |
|-------|--------|-----------------|
| Green Agent Prize | $3,000 | PRIMARY — auto-deferral action closes the gap |
| Sustainable Design Bonus | $500 | Already largely won — quantized NLP, metrics documented |
| Most Impactful | $5,000 | STRETCH — needs demonstrable large-scale savings |
| Anthropic Category | $10,000 | Requires Claude API integration (one new endpoint) |
| Google Cloud Category | $10,000 | Requires Gemini API integration (alternative to Anthropic) |
| Easiest to Use | $5,000 | One-click install template + GitLab Duo YAML |

The `$10k Anthropic/Google category prize` is the most financially significant and requires the least architectural work: one new endpoint + one new API key.

---

## Recommendations: What to Build

### ✅ BUILD — Feature 1: Closed-Loop Auto-Deferral (CRITICAL)

**Why this wins:** Transforms GreenPipe from a "carbon calculator" into an "autonomous green agent." Directly addresses the action-vs-recommendation gap. This is the feature that changes the demo narrative from "here is a report" to "here is what I did."

**The user experience change:**
- **Before (current):** Pipeline finishes → GreenPipe posts comment: "This is deferrable. Consider rescheduling."
- **After:** Pipeline finishes → GreenPipe detects deferrable urgency + high carbon → **cancels the pipeline + creates a GitLab Pipeline Schedule for the optimal low-carbon window** → posts MR comment: "⏸️ I automatically rescheduled this pipeline from now (386 gCO₂e) to 03:00 UTC (212 gCO₂e) — 45% greener. Override with `@greenpipe run-now`."

**Three safe modes:**
1. `recommend-only` (default, current behaviour) — never touches the pipeline, only advises
2. `approval-required` — posts comment with `@greenpipe confirm-defer` prompt, waits for human approval via next webhook mention
3. `auto-execute` — cancels + schedules automatically when policy thresholds are met

**Policy guardrails (all configurable via env vars):**
- `GREENPIPE_DEFER_MODE` — one of `recommend-only` / `approval-required` / `auto-execute`
- `GREENPIPE_MIN_SAVINGS_PCT` — minimum carbon intensity reduction to trigger deferral (default: 20%)
- `GREENPIPE_MAX_DELAY_HOURS` — maximum hours a pipeline can be deferred (default: 24)
- `GREENPIPE_PROTECTED_BRANCHES` — comma-separated list; never defer these (default: `main,master,release*`)
- `GREENPIPE_PROTECTED_ENVS` — never defer pipelines tagged with these environment names (default: `production,staging`)

**Integration points in the current codebase:**

1. **`src/config.py`** — add 5 new settings fields
2. **`src/services/gitlab_client.py`** — add 3 new methods:
   - `cancel_pipeline(project_id, pipeline_id)` — calls `project.pipelines.get(id).cancel()`
   - `create_pipeline_schedule(project_id, ref, cron, description)` — calls `project.pipeline_schedules.create()`
   - `is_protected_branch(project_id, ref)` — checks ref against policy list
3. **`src/api/agent_routes.py`** — modify `webhook_pipeline_event()`:
   - After analysis, if `can_defer=True` and mode is not `recommend-only`, call deferral logic
   - Add handling for `@greenpipe run-now`, `@greenpipe defer <hours>`, `@greenpipe confirm-defer` in `webhook_mention_event()`
4. **`src/models/pipeline.py`** + **Alembic migration** — add `DeferralAuditRecord` table:
   - Fields: `pipeline_id`, `project_id`, `original_intensity`, `target_window`, `predicted_savings_pct`, `action_taken` (`none`/`scheduled`/`cancelled`), `urgency_class`, `policy_mode`, `created_at`
   - This is the "reproducible decision logic" judges want to see
5. **`src/api/report_formatter.py`** — add new section for auto-deferral confirmation in MR comment

**Tests to write:** ~10-12 new tests in `test_agent_routes.py` covering:
- policy: protected branch blocks auto-execute
- policy: savings below threshold blocks auto-execute
- approve-required: first webhook posts prompt, second triggers action
- audit record is written in all modes

**Effort estimate:** Medium-High (most complex integration, but builds directly on existing patterns)

---

### ✅ BUILD — Feature 2: Anthropic Claude LLM "Green Code Profiler" (HIGH PRIZE IMPACT)

**Why this wins:** Unlocks the $10,000 Anthropic category prize. Adds a qualitatively new capability (code-level efficiency analysis) that is complementary to the existing quantitative SCI measurement. The story becomes: "GreenPipe uses a tiny INT8 DistilBERT for fast, frequent urgency routing (58% less energy), and calls Claude only for deep, on-demand code quality analysis — demonstrating hybrid AI architecture."

**What it does:** When `@greenpipe optimize` is posted in an MR comment, GreenPipe:
1. Fetches the git diff for the MR via GitLab API
2. Sends the diff to `claude-sonnet-4-6` with a green software engineering prompt
3. Returns a structured list of energy-efficiency suggestions (e.g., "N+1 query on line 47 — replace with eager loading to eliminate repeated DB calls")
4. Posts the result as an MR comment with estimated energy impact per suggestion

**Integration points:**

1. **`pyproject.toml`** — add `anthropic>=0.34.0` as a dependency
2. **`src/config.py`** — add `anthropic_api_key: str = ""`
3. **`src/services/code_analyzer.py`** (NEW FILE) — thin wrapper around the Anthropic client:
   - `analyze_diff_for_efficiency(diff_text: str) -> list[EfficiencySuggestion]`
   - Structured output using Claude's tool use / JSON mode
   - Gracefully disabled when `ANTHROPIC_API_KEY` is blank
4. **`src/api/agent_schemas.py`** — add `AnalyzeCodeEfficiencyInput` / `AnalyzeCodeEfficiencyOutput` schemas
5. **`src/api/agent_routes.py`** — add `POST /agent/tools/analyze_code_efficiency` endpoint
6. **`src/services/gitlab_client.py`** — add `get_mr_diff(project_id, mr_iid)` method
7. **Mention webhook** — add `@greenpipe optimize` command
8. **`src/api/report_formatter.py`** — `format_code_efficiency_comment()` function

**What Claude receives (the prompt):**
- System: "You are a green software engineering assistant. Analyse this code diff for energy efficiency issues: N+1 queries, missing caching, unbounded loops, synchronous I/O where async would reduce idle CPU time, over-computation, etc. Return a JSON list of suggestions with: line_range, issue_type, description, estimated_energy_impact (low/medium/high), suggested_fix."
- User: `<git diff content>`

**Output schema:**
```
{
  "suggestions": [
    {
      "file": "src/api/routes.py",
      "line_range": "47-53",
      "issue_type": "n+1_query",
      "description": "...",
      "estimated_energy_impact": "medium",
      "suggested_fix": "..."
    }
  ],
  "overall_assessment": "...",
  "estimated_energy_reduction": "10-30%"
}
```

**Tests to write:** ~8 tests, mocking the Anthropic client (similar to how we mock the Carbon Aware SDK).

**Effort estimate:** Medium (new service class + 1 endpoint + tests; Anthropic SDK is well-documented)

---

### ✅ BUILD — Feature 3: MR Comment UX Overhaul (HIGH PRIZE IMPACT, LOW EFFORT)

**Why this wins:** The "Easiest to Use" prize requires excellent UX. The current MR comment is good but verbose. A compact summary card + clear action buttons + better command discovery dramatically improves first impressions.

**Changes to `src/api/report_formatter.py`:**

**New compact summary card at the top** (replaces the current long header):
```
## 🌱 GreenPipe Carbon Report — 4.83 gCO₂e | 🟢 Deferrable | 45% savings available

| SCI Score | Energy | Carbon Intensity | Urgency | Recommended Action |
|-----------|--------|-----------------|---------|-------------------|
| 4.83 gCO₂e | 0.0119 kWh | 386 gCO₂e/kWh | 🟢 Deferrable | Defer to 03:00 UTC |
```

**New "Developer Impact" section** (addresses the "AI Paradox" suggestion):
```
### 💡 Developer Time Saved
This analysis ran automatically — no manual setup required.
- ⚡ Pipeline analysis: automated (saved ~5 min manual ECO-CI run)
- 🗓️ Scheduling recommendation: automated (saved ~10 min grid research)
- 📊 Historical tracking: automated (would require a spreadsheet without GreenPipe)
```

**New action commands section** in MR comment (since GitLab doesn't support real buttons in notes, make the commands obvious):
```
### 🎮 Available Commands
Reply with one of:
| Command | Effect |
|---------|--------|
| `@greenpipe run-now` | Override deferral — run pipeline immediately |
| `@greenpipe defer 4h` | Defer by 4 hours (default: optimal window) |
| `@greenpipe optimize` | Analyze this MR's code for energy inefficiencies |
| `@greenpipe leaderboard` | Show carbon-efficiency rankings for this project |
| `@greenpipe why` | Explain the urgency classification decision |
```

**New `@greenpipe why` command** — returns the explanation of why a pipeline was classified urgent/normal/deferrable:
- Which keywords matched (if keyword fallback) or which tokens had highest attention (if NLP model)
- Confidence score with plain-English interpretation
- Override suggestion if classification seems wrong

**Update the `format_help_comment()` to include new commands.**

**Effort estimate:** Low (all changes in one file + regex pattern additions in agent_routes.py)

---

### ✅ BUILD — Feature 4: GitLab Duo Agent YAML + One-Click Install (LOW EFFORT, HIGH COMPLIANCE)

**Why this wins:** The hackathon explicitly evaluates GitLab Duo Agent Platform compliance. Having the correct agent YAML definition files and a CI/CD component template directly addresses the "Easiest to Use" prize criteria.

**Files to create:**

**`.gitlab/agents/greenpipe/config.yaml`** (GitLab Duo Agent Platform registration):
```yaml
# Required by GitLab Duo Agent Platform
agent:
  name: greenpipe
  description: "GSF-compliant carbon-aware CI/CD agent"
  tools:
    - name: analyze_pipeline
      url: ${GREENPIPE_API_URL}/agent/tools/analyze_pipeline
    - name: generate_sci_report
      url: ${GREENPIPE_API_URL}/agent/tools/generate_sci_report
    - name: suggest_scheduling
      url: ${GREENPIPE_API_URL}/agent/tools/suggest_scheduling
    - name: classify_urgency
      url: ${GREENPIPE_API_URL}/agent/tools/classify_urgency
    - name: analyze_code_efficiency
      url: ${GREENPIPE_API_URL}/agent/tools/analyze_code_efficiency
```

**`templates/greenpipe-ci.yml`** (GitLab CI/CD Component template):
```yaml
# Include this in your .gitlab-ci.yml:
# include:
#   - project: 'archit1706/green-pipe'
#     ref: main
#     file: 'templates/greenpipe-ci.yml'

greenpipe-setup:
  stage: .pre
  script:
    - echo "GreenPipe webhook configured at ${GREENPIPE_API_URL}"
  variables:
    GREENPIPE_API_URL: ""  # Set in CI/CD variables
```

**README section** — "One-Click Installation" showing the `include:` syntax.

**Effort estimate:** Very Low (just YAML files + README update)

---

### ✅ BUILD — Feature 5: Multi-Region Carbon Comparison (MEDIUM EFFORT, DIFFERENTIATOR)

**Why this wins:** Current scheduling is "best window for one location." Multi-region says "best location + best window" — a qualitatively stronger recommendation and a differentiator vs. any other hackathon entry.

**What it adds to `suggest_scheduling`:**
- Query the Carbon Aware SDK for 3-5 candidate locations simultaneously
- Return a ranked list with estimated carbon intensity at the optimal window for each
- Include current intensity, best window intensity, and carbon savings for each
- Include a Pareto summary: "Region A is 40% greener than your current region; Region B is 25% greener but available 2 hours sooner"
- Policy filter: only show regions in `GREENPIPE_ALLOWED_REGIONS`

**Integration points:**

1. **`src/services/carbon_service.py`** — add `compare_regions(locations: list[str], duration_minutes: int) -> list[RegionComparison]`
2. **`src/api/agent_schemas.py`** — add `CompareRegionsInput` / `CompareRegionsOutput`
3. **`src/api/agent_routes.py`** — add `POST /agent/tools/compare_regions`
4. **Mention webhook** — add `@greenpipe regions` command
5. **`src/api/report_formatter.py`** — multi-region table section in scheduling recommendation

**Candidate region defaults:** `us-east1`, `us-west1`, `europe-west1`, `asia-southeast1`, `australia-southeast1`

**Effort estimate:** Medium (carbon service already has all the HTTP logic, this is parallel queries + sorting)

---

### ✅ BUILD — Feature 6: Leaderboard + Contributor Impact (MEDIUM EFFORT, UX WOW FACTOR)

**Why this wins:** "Easiest to Use" and "Most Impactful" prizes both benefit from features that make developers actively want to use GreenPipe. Gamification is proven UX. A leaderboard makes carbon tracking feel competitive and rewarding.

**`@greenpipe leaderboard` returns:**
```
## 🏆 GreenPipe Carbon Leaderboard — Top 5 Green Contributors (This Month)

| Rank | Contributor | Pipelines | Avg SCI | Deferred | CO₂e Saved |
|------|------------|-----------|---------|----------|------------|
| 🥇 1 | Alice M.   | 23 runs   | 2.1 gCO₂e | 8 (35%) | 12.4 g |
| 🥈 2 | Bob K.     | 17 runs   | 2.8 gCO₂e | 6 (35%) | 9.1 g |
```

**Integration points:**

1. **Alembic migration** — add `author_name` column to `pipeline_runs` table (populated from `CommitData.author_name` already available in `PipelineAnalyzer._run_analysis()`)
2. **`src/api/analytics_routes.py`** — add `GET /analytics/leaderboard` endpoint
3. **`src/api/analytics_schemas.py`** — add `LeaderboardEntry`, `LeaderboardResponse`
4. **`src/api/agent_routes.py`** — add `@greenpipe leaderboard` mention command
5. **`src/api/report_formatter.py`** — `format_leaderboard_comment()` function

**Effort estimate:** Medium (requires DB schema migration + new analytics query + new route)

---

## ❌ SKIP — What Not to Build

### Skip: Smart CI/CD Job Pruning (Idea 4)
**Why skip:** Cancelling individual *jobs* within a running pipeline requires GitLab CI job-level token scopes that are different from the pipeline-level scopes we already have. It also requires real-time job monitoring (polling during pipeline execution), which is an architectural shift. The risk of accidentally pruning critical jobs in production is high. **The auto-deferral feature (Feature 1) already delivers the "action" story without this complexity.**

### Skip: Cost + Carbon Co-optimization (Idea 8)
**Why skip:** Cost per minute data for GitLab SaaS runners is not publicly available in a reliable, current form (pricing changes, varies by plan). Using stale estimates would look bad in judging. The cost framing also dilutes the green software story. **Focus the narrative on carbon — it's more compelling for a green software hackathon.**

### Skip: Full Explainability / LIME/SHAP for NLP (Idea 3, partial)
**Why skip:** Full attention-based token importance requires loading the full FP32 model (defeats the INT8 quantization benefit) or running a separate interpretation model. The `@greenpipe why` command in Feature 3's UX overhaul gives the same judge-visible explainability through keyword matching + confidence scores without the technical risk.

---

## Implementation Order

Based on effort, risk, and prize impact:

| Priority | Feature | Effort | Prize Category | Deadline Risk |
|----------|---------|--------|---------------|---------------|
| 1 | Auto-Deferral + Policy Config (Feature 1) | High | Green Agent $3k | Critical — builds on existing webhook flow |
| 2 | Claude LLM Code Profiler (Feature 2) | Medium | Anthropic $10k | One new service file |
| 3 | MR Comment UX Overhaul (Feature 3) | Low | Easiest to Use $5k | Single file edit |
| 4 | GitLab Duo YAML + CI Template (Feature 4) | Very Low | Platform compliance | Config files only |
| 5 | Multi-Region Scheduling (Feature 5) | Medium | Differentiator | Extends existing carbon service |
| 6 | Leaderboard (Feature 6) | Medium | UX / Impactful | Requires DB migration |

**Recommended execution order:**
1. Feature 4 first — zero risk, policy compliance, can be done in 30 minutes
2. Feature 3 next — low risk, immediate UX improvement, no new dependencies
3. Feature 1 — highest risk, most complex, do it before anything else that touches webhooks
4. Feature 2 — new dependency, isolated service, can be developed in parallel
5. Feature 5 — extends existing carbon service, moderate risk
6. Feature 6 — DB migration at the end to avoid destabilising analytics tests

---

## Files Summary: What Changes and What's New

### New files:
- `.gitlab/agents/greenpipe/config.yaml` — Duo Agent platform YAML (Feature 4)
- `templates/greenpipe-ci.yml` — one-click CI/CD component (Feature 4)
- `src/services/code_analyzer.py` — Anthropic Claude integration (Feature 2)
- `alembic/versions/xxx_add_deferral_audit_and_author.py` — migration for new DB columns (Features 1 + 6)

### Modified files:
- `src/config.py` — add 6 new settings fields (Features 1, 2)
- `.env.example` — add new env vars (Features 1, 2)
- `src/services/gitlab_client.py` — add 4 new methods (Features 1, 2, 6)
- `src/services/carbon_service.py` — add `compare_regions()` (Feature 5)
- `src/api/agent_schemas.py` — add 3 new input/output schema pairs (Features 1, 2, 5)
- `src/api/agent_routes.py` — update pipeline webhook + add 4 new mention commands + 2 new tool endpoints (Features 1, 2, 3, 5)
- `src/api/analytics_routes.py` — add leaderboard endpoint (Feature 6)
- `src/api/analytics_schemas.py` — add leaderboard schemas (Feature 6)
- `src/api/report_formatter.py` — compact card + developer impact + action commands (Feature 3)
- `src/models/pipeline.py` — add `author_name` field (Feature 6)
- `AGENTS.md` — update tool list + add new tools
- `README.md` — add new endpoints, one-click install, Anthropic prize mention
- `SUBMISSION.md` — update "What It Does" and "Built With" sections
- `docs/COMPLIANCE_CHECKLIST.md` — add new checklist items

### New test files:
- `tests/test_auto_deferral.py` — ~12 tests for auto-deferral logic and policy enforcement (Feature 1)
- `tests/test_code_analyzer.py` — ~8 tests for Claude integration with mocked API (Feature 2)
- `tests/test_multi_region.py` — ~6 tests for region comparison (Feature 5)
- `tests/test_leaderboard.py` — ~6 tests for leaderboard analytics (Feature 6)

---

## Expected Test Count After All Features

| Test file | Current | New | Total |
|-----------|---------|-----|-------|
| test_agent_routes.py | 35 | +12 (deferral) | 47 |
| test_analytics.py | 37 | +6 (leaderboard) | 43 |
| test_auto_deferral.py | 0 | +12 | 12 |
| test_code_analyzer.py | 0 | +8 | 8 |
| test_multi_region.py | 0 | +6 | 6 |
| Existing (integration/energy/sci/nlp) | 51 | 0 | 51 |
| **Total** | **123** | **+44** | **~167** |

---

## Submission Story After All Features

**Inspiration:** GSF standards exist but GitLab has no autonomous green agent.

**What it does:**
1. Measures every pipeline automatically using 3 GSF standards
2. Classifies urgency with INT8 DistilBERT (58% less energy than FP32)
3. **Autonomously reschedules deferrable pipelines to low-carbon windows** ← NEW ACTION
4. Profiles code for energy inefficiencies using Claude claude-sonnet-4-6 ← NEW LLM TOOL
5. Compares multi-region scheduling options for optimal carbon + time tradeoffs ← NEW
6. Gamifies developer sustainability with a carbon leaderboard ← NEW

**Prize eligibility after:**
- Green Agent ($3k) — closed-loop auto-deferral
- Sustainable Design ($500) — quantized NLP + own carbon measurement
- Anthropic Category ($10k) — Claude code profiler tool
- Easiest to Use ($5k) — one-click CI/CD template + UX overhaul
- Most Impactful ($5k) — leaderboard + multi-region + demonstrated savings

---

## What This Plan Does NOT Include

- No changes to the energy estimation methodology (Teads curve is correct and validated)
- No changes to the SCI calculator (ISO/IEC 21031:2024 implementation is complete)
- No changes to existing test logic (all 123 tests continue to pass)
- No breaking API changes (all new endpoints are additive)
- No new infrastructure requirements (Anthropic API key is the only new external dependency)
