"""
Markdown report formatter for GitLab MR / issue comments.

Converts a PipelineAnalysisReport into a richly formatted GitLab-flavoured
markdown comment that the agent posts after each pipeline analysis.
"""

from __future__ import annotations

from src.api.agent_schemas import DeferralDecision
from src.services.pipeline_analyzer import PipelineAnalysisReport

# Urgency → emoji + short label
_URGENCY_BADGES = {
    "urgent":     "🔴 **Urgent** — run immediately, do not defer",
    "normal":     "🟡 **Normal** — proceed on schedule",
    "deferrable": "🟢 **Deferrable** — consider carbon-aware scheduling",
}

_SCI_LABEL = "SCI (ISO/IEC 21031:2024)"


def format_mr_comment(report: PipelineAnalysisReport) -> str:
    """
    Return a GitLab markdown string ready to be posted as an MR comment.

    Sections:
      1. Header + urgency badge
      2. SCI scorecard
      3. Per-job energy table
      4. Carbon intensity info
      5. Scheduling recommendation (with best window if available)
      6. GSF standards footer
    """
    lines: list[str] = []

    # -----------------------------------------------------------------------
    # 1. Header
    # -----------------------------------------------------------------------
    pipeline_ref = f"pipeline #{report.gitlab_pipeline_id}" if report.gitlab_pipeline_id else "pipeline"
    lines += [
        "## 🌱 GreenPipe Carbon Report",
        "",
        f"Analysis for **{pipeline_ref}** "
        f"(`{report.pipeline_ref or 'unknown ref'}` @ "
        f"`{(report.pipeline_sha or '')[:8] or 'unknown sha'}`) "
        f"— analysed at {report.analyzed_at.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"**Urgency:** {_URGENCY_BADGES.get(report.urgency_class, report.urgency_class)} "
        f"*(confidence {report.urgency_confidence:.0%})*",
        "",
        "---",
        "",
    ]

    # -----------------------------------------------------------------------
    # 2. SCI scorecard
    # -----------------------------------------------------------------------
    sci = report.sci
    lines += [
        f"### 📊 {_SCI_LABEL}",
        "",
        "| Metric | Value |",
        "| ------ | ----- |",
        f"| **SCI Score** | `{sci.sci_score:.4f} gCO₂e / pipeline_run` |",
        f"| Operational carbon | `{sci.operational_carbon_gco2:.4f} gCO₂e` |",
        f"| Embodied carbon (M) | `{sci.embodied_carbon_gco2:.4f} gCO₂e` |",
        f"| Total carbon | `{sci.total_carbon_gco2:.4f} gCO₂e` |",
        f"| Total energy (E) | `{report.total_energy_kwh:.6f} kWh` |",
        f"| Carbon intensity (I) | `{report.carbon_intensity_gco2_kwh:.1f} gCO₂e/kWh` |",
        "",
        "<details>",
        "<summary>Formula details</summary>",
        "",
        "```",
        "SCI = ((E × I) + M) / R",
        f"    = (({report.total_energy_kwh:.6f} × {report.carbon_intensity_gco2_kwh:.1f}) "
        f"+ {sci.embodied_carbon_gco2:.4f}) / 1",
        f"    = {sci.operational_carbon_gco2:.4f} + {sci.embodied_carbon_gco2:.4f}",
        f"    = {sci.sci_score:.4f} gCO₂e",
        "```",
        "",
        "</details>",
        "",
        "---",
        "",
    ]

    # -----------------------------------------------------------------------
    # 3. Per-job energy table
    # -----------------------------------------------------------------------
    lines += [
        "### ⚡ Energy Breakdown",
        "",
        "| Job | Stage | Duration | Runner | CPU% | TDP Factor | Energy (kWh) |",
        "| --- | ----- | -------- | ------ | ---- | ---------- | ------------ |",
    ]
    for jr in report.job_reports:
        lines.append(
            f"| `{jr.job_name or 'unknown'}` "
            f"| {jr.stage or '—'} "
            f"| {jr.duration_seconds:.0f}s "
            f"| `{jr.runner_type or 'unknown'}` "
            f"| {jr.cpu_utilization_percent:.0f}% "
            f"| {jr.tdp_factor:.3f} "
            f"| `{jr.energy_kwh:.6f}` |"
        )
    lines += [
        "",
        f"**Total energy:** `{report.total_energy_kwh:.6f} kWh`  "
        f"*(methodology: {report.energy_methodology})*",
        "",
        "---",
        "",
    ]

    # -----------------------------------------------------------------------
    # 4. Carbon intensity
    # -----------------------------------------------------------------------
    lines += [
        "### 🏭 Carbon Intensity",
        "",
        f"- **Location:** `{report.runner_location}`",
        f"- **Intensity:** `{report.carbon_intensity_gco2_kwh:.1f} gCO₂e/kWh`",
        f"- **Source:** {report.carbon_data_source}",
        "",
        "---",
        "",
    ]

    # -----------------------------------------------------------------------
    # 5. Scheduling recommendation
    # -----------------------------------------------------------------------
    lines += [
        "### 🕐 Scheduling Recommendation",
        "",
        f"> {report.scheduling_message}",
        "",
    ]
    if report.can_defer and report.scheduling_window:
        w = report.scheduling_window
        saved_co2 = report.carbon_saved_if_deferred_gco2()
        lines += [
            "| Recommended window | Intensity | Savings |",
            "| ------------------ | --------- | ------- |",
            f"| `{w.timestamp or 'TBD'}` "
            f"| `{w.intensity_gco2_kwh:.1f} gCO₂e/kWh` "
            f"| `{w.savings_percent:.1f}%`"
            + (f" ≈ `{saved_co2:.4f} gCO₂e`" if saved_co2 is not None else "")
            + " |",
            "",
        ]

    lines += ["---", ""]

    # -----------------------------------------------------------------------
    # 6. GSF standards footer
    # -----------------------------------------------------------------------
    lines += [
        "<details>",
        "<summary>📋 GSF Standards Used</summary>",
        "",
    ]
    for std in report.gsf_standards_used:
        lines.append(f"- {std}")
    lines += [
        "",
        "</details>",
        "",
        "---",
        "",
        "*Generated by [🌱 GreenPipe](https://github.com/greenpipe) "
        "— built on [Green Software Foundation](https://greensoftware.foundation/) standards*",
    ]

    return "\n".join(lines)


def format_deferral_comment(deferral: DeferralDecision) -> str:
    """
    Return a markdown section describing the auto-deferral action or recommendation.

    Appended to the main MR comment when deferral is relevant.
    """
    _ACTION_ICONS = {
        "deferred": "⏸️",
        "awaiting_approval": "⏳",
        "recommended": "💡",
    }
    icon = _ACTION_ICONS.get(deferral.action, "ℹ️")
    lines: list[str] = [
        f"### {icon} Auto-Deferral Decision",
        "",
    ]

    if deferral.action == "deferred":
        lines += [
            f"> **Pipeline auto-deferred.** Cancelled and rescheduled to "
            f"`{deferral.target_window}` (cron: `{deferral.schedule_cron}`).",
            ">",
            f"> Predicted carbon savings: **{deferral.predicted_savings_pct:.1f}%** "
            f"({deferral.original_intensity_gco2_kwh} → "
            f"{deferral.target_intensity_gco2_kwh} gCO₂e/kWh).",
            ">",
            "> Override: reply `@greenpipe run-now` to run immediately.",
            "",
        ]
    elif deferral.action == "awaiting_approval":
        lines += [
            f"> **Deferral pending your approval.** A {deferral.predicted_savings_pct:.1f}% "
            f"carbon savings window was found at `{deferral.target_window}`.",
            ">",
            "> Reply `@greenpipe confirm-defer` to approve or "
            "`@greenpipe run-now` to skip.",
            "",
        ]
    elif deferral.action == "recommended":
        lines += [
            f"> **Deferral recommended.** {deferral.predicted_savings_pct:.1f}% "
            f"lower carbon intensity available at `{deferral.target_window}`.",
            ">",
            "> Reply `@greenpipe defer` to reschedule or "
            "`@greenpipe run-now` to proceed.",
            "",
        ]

    lines += [
        f"*Policy mode: `{deferral.policy_mode}` — "
        f"[configure](https://github.com/greenpipe#auto-deferral-policy)*",
    ]
    return "\n".join(lines)


def format_help_comment() -> str:
    """Return the @greenpipe help text posted in response to @greenpipe help."""
    return (
        "## 🌱 GreenPipe — Available Commands\n\n"
        "| Command | Description |\n"
        "| ------- | ----------- |\n"
        "| `@greenpipe analyze` | Analyze the latest pipeline for this MR |\n"
        "| `@greenpipe report` | Generate a full GSF SCI report |\n"
        "| `@greenpipe schedule` | Show carbon-optimal execution windows |\n"
        "| `@greenpipe defer` | Defer the pipeline to the best low-carbon window |\n"
        "| `@greenpipe run-now` | Override deferral — run the pipeline immediately |\n"
        "| `@greenpipe confirm-defer` | Approve a pending deferral |\n"
        "| `@greenpipe why` | Explain the urgency classification decision |\n"
        "| `@greenpipe help` | Show this help message |\n\n"
        "*[GreenPipe](https://github.com/greenpipe) implements "
        "[GSF SCI](https://sci.greensoftware.foundation/), "
        "[Carbon Aware SDK](https://github.com/Green-Software-Foundation/carbon-aware-sdk), "
        "and [Impact Framework](https://if.greensoftware.foundation/) standards.*"
    )
