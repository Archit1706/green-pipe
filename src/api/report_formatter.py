"""
Markdown report formatter for GitLab MR / issue comments.

Converts a PipelineAnalysisReport into a richly formatted GitLab-flavoured
markdown comment that the agent posts after each pipeline analysis.
"""

from __future__ import annotations

from src.api.agent_schemas import DeferralDecision
from src.services.carbon_service import RegionComparison
from src.services.code_analyzer import CodeAnalysisResult
from src.services.pipeline_analyzer import PipelineAnalysisReport

# Urgency → emoji + short label
_URGENCY_BADGES = {
    "urgent":     "🔴 **Urgent** — run immediately, do not defer",
    "normal":     "🟡 **Normal** — proceed on schedule",
    "deferrable": "🟢 **Deferrable** — consider carbon-aware scheduling",
}

# Compact urgency badges for the summary card header
_URGENCY_SHORT = {
    "urgent":     "🔴 Urgent",
    "normal":     "🟡 Normal",
    "deferrable": "🟢 Deferrable",
}

_SCI_LABEL = "SCI (ISO/IEC 21031:2024)"


def format_mr_comment(report: PipelineAnalysisReport) -> str:
    """
    Return a GitLab markdown string ready to be posted as an MR comment.

    Sections:
      1. Compact summary card (header + one-row scorecard)
      2. SCI scorecard (collapsible detail)
      3. Per-job energy table (collapsible)
      4. Carbon intensity info
      5. Scheduling recommendation (with best window if available)
      6. Developer Impact section
      7. Available Commands
      8. GSF standards footer
    """
    lines: list[str] = []

    sci = report.sci
    urgency_short = _URGENCY_SHORT.get(report.urgency_class, report.urgency_class)

    # -----------------------------------------------------------------------
    # 1. Compact summary card
    # -----------------------------------------------------------------------

    # Build savings string for header
    savings_str = ""
    if report.can_defer and report.scheduling_window:
        savings_str = f" | {report.scheduling_window.savings_percent:.0f}% savings available"

    # Build recommended action string
    if report.urgency_class == "urgent":
        action_str = "Run immediately"
    elif report.can_defer and report.scheduling_window:
        action_str = f"Defer to `{report.scheduling_window.timestamp or 'TBD'}`"
    elif report.urgency_class == "deferrable":
        action_str = "Deferrable (no window found)"
    else:
        action_str = "Proceed on schedule"

    pipeline_ref = (
        f"pipeline #{report.gitlab_pipeline_id}" if report.gitlab_pipeline_id else "pipeline"
    )
    lines += [
        f"## 🌱 GreenPipe Carbon Report — "
        f"{sci.sci_score:.2f} gCO₂e | {urgency_short}{savings_str}",
        "",
        f"| SCI Score | Energy | Carbon Intensity | Urgency | Recommended Action |",
        f"|-----------|--------|-----------------|---------|-------------------|",
        f"| `{sci.sci_score:.4f} gCO₂e` "
        f"| `{report.total_energy_kwh:.6f} kWh` "
        f"| `{report.carbon_intensity_gco2_kwh:.1f} gCO₂e/kWh` "
        f"| {urgency_short} ({report.urgency_confidence:.0%}) "
        f"| {action_str} |",
        "",
        f"*{pipeline_ref} · "
        f"`{report.pipeline_ref or 'unknown ref'}` @ "
        f"`{(report.pipeline_sha or '')[:8] or 'unknown sha'}` · "
        f"{report.analyzed_at.strftime('%Y-%m-%d %H:%M UTC')}*",
        "",
        "---",
        "",
    ]

    # -----------------------------------------------------------------------
    # 2. SCI scorecard (collapsible)
    # -----------------------------------------------------------------------
    lines += [
        "<details>",
        f"<summary>📊 {_SCI_LABEL} — Full Breakdown</summary>",
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
    ]

    # -----------------------------------------------------------------------
    # 3. Per-job energy table (collapsible)
    # -----------------------------------------------------------------------
    lines += [
        "<details>",
        f"<summary>⚡ Energy Breakdown — {len(report.job_reports)} job(s), "
        f"{report.total_energy_kwh:.6f} kWh total</summary>",
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
        "</details>",
        "",
    ]

    # -----------------------------------------------------------------------
    # 4. Carbon intensity
    # -----------------------------------------------------------------------
    lines += [
        f"### 🏭 Carbon Intensity — "
        f"`{report.carbon_intensity_gco2_kwh:.1f} gCO₂e/kWh` "
        f"@ `{report.runner_location}`",
        "",
        f"- **Source:** {report.carbon_data_source}",
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
            "| Recommended Window | Intensity | Savings |",
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
    # 6. Developer Impact section
    # -----------------------------------------------------------------------
    lines += [
        "### 💡 Developer Time Saved",
        "",
        "This analysis ran automatically — no manual setup required.",
        "",
        "- ⚡ **Pipeline analysis:** automated *(saved ~5 min manual ECO-CI run)*",
        "- 🗓️ **Scheduling recommendation:** automated *(saved ~10 min grid carbon research)*",
        "- 📊 **Historical tracking:** automated *(would require a spreadsheet without GreenPipe)*",
        "",
        "---",
        "",
    ]

    # -----------------------------------------------------------------------
    # 7. Available Commands
    # -----------------------------------------------------------------------
    lines += [
        "### 🎮 Available Commands",
        "",
        "Reply with one of:",
        "",
        "| Command | Effect |",
        "|---------|--------|",
        "| `@greenpipe run-now` | Override deferral — run pipeline immediately |",
        "| `@greenpipe defer` | Defer to the best low-carbon window |",
        "| `@greenpipe optimize` | Analyse this MR's code for energy inefficiencies |",
        "| `@greenpipe regions` | Compare carbon intensity across multiple regions |",
        "| `@greenpipe why` | Explain the urgency classification decision |",
        "| `@greenpipe schedule` | Show carbon-optimal execution windows |",
        "| `@greenpipe help` | List all available commands |",
        "",
        "---",
        "",
    ]

    # -----------------------------------------------------------------------
    # 8. GSF standards footer
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


_IMPACT_ICONS = {
    "high": "🔴",
    "medium": "🟡",
    "low": "🟢",
}


def format_code_efficiency_comment(result: CodeAnalysisResult) -> str:
    """
    Return a GitLab markdown comment for a code efficiency analysis result.

    Posted in response to ``@greenpipe optimize``.
    """
    lines: list[str] = [
        "## 🔬 GreenPipe Code Efficiency Analysis",
        "",
    ]

    if result.error and not result.suggestions:
        lines += [
            f"> ⚠️ {result.error}",
            "",
        ]
        return "\n".join(lines)

    if not result.suggestions:
        lines += [
            "> ✅ **No energy inefficiencies detected.** This diff looks efficient!",
            "",
        ]
        if result.overall_assessment:
            lines += [f"> {result.overall_assessment}", ""]
    else:
        lines += [
            f"Found **{len(result.suggestions)} suggestion(s)** "
            f"for reducing energy consumption:",
            "",
        ]

        for i, s in enumerate(result.suggestions, 1):
            icon = _IMPACT_ICONS.get(s.estimated_energy_impact, "ℹ️")
            lines += [
                f"### {icon} {i}. {s.issue_type.replace('_', ' ').title()} "
                f"— `{s.file}` (lines {s.line_range})",
                "",
                f"**Impact:** {s.estimated_energy_impact.upper()}",
                "",
                f"{s.description}",
                "",
                f"**Suggested fix:** {s.suggested_fix}",
                "",
                "---",
                "",
            ]

        if result.overall_assessment:
            lines += [
                "### 📋 Overall Assessment",
                "",
                result.overall_assessment,
                "",
            ]

        if result.estimated_energy_reduction:
            lines += [
                f"**Estimated energy reduction if all suggestions applied:** "
                f"`{result.estimated_energy_reduction}`",
                "",
            ]

    # Footer
    model_info = f"Model: `{result.model_used}`" if result.model_used else ""
    tokens_info = f" | Tokens: `{result.tokens_used}`" if result.tokens_used else ""
    lines += [
        "<details>",
        "<summary>Analysis metadata</summary>",
        "",
        f"- {model_info}{tokens_info}",
        "- Powered by Anthropic Claude — hybrid AI architecture "
        "(DistilBERT INT8 for urgency, Claude for deep code analysis)",
        "",
        "</details>",
        "",
        "*Generated by [🌱 GreenPipe](https://github.com/greenpipe) "
        "— [Anthropic Claude](https://www.anthropic.com/) green code profiling*",
    ]

    return "\n".join(lines)


_RANK_ICONS = {1: "🥇", 2: "🥈", 3: "🥉"}


def format_regions_comment(regions: list[RegionComparison]) -> str:
    """
    Return a GitLab markdown comment for a multi-region carbon comparison.

    Posted in response to ``@greenpipe regions`` or the compare_regions tool.
    """
    lines: list[str] = [
        "## 🌍 GreenPipe — Multi-Region Carbon Comparison",
        "",
    ]

    if not regions:
        lines += [
            "> ⚠️ No regions were compared. Check Carbon Aware SDK configuration.",
            "",
        ]
        return "\n".join(lines)

    # Build comparison table
    lines += [
        "| Rank | Region | Current Intensity | Best Window | Window Intensity | Savings |",
        "| ---- | ------ | ----------------- | ----------- | ---------------- | ------- |",
    ]

    for r in regions:
        rank_icon = _RANK_ICONS.get(r.rank, f"{r.rank}.")
        if r.error:
            lines.append(
                f"| {rank_icon} | `{r.display_name}` | ⚠️ error | — | — | — |"
            )
            continue

        best_ts = f"`{r.best_window_timestamp}`" if r.best_window_timestamp else "—"
        best_int = (
            f"`{r.best_window_intensity_gco2_kwh:.1f} gCO₂e/kWh`"
            if r.best_window_intensity_gco2_kwh is not None
            else "—"
        )
        savings = (
            f"**{r.savings_vs_current_pct:.1f}%**"
            if r.savings_vs_current_pct > 0
            else "—"
        )
        lines.append(
            f"| {rank_icon} "
            f"| `{r.display_name}` "
            f"| `{r.current_intensity_gco2_kwh:.1f} gCO₂e/kWh` "
            f"| {best_ts} "
            f"| {best_int} "
            f"| {savings} |"
        )

    lines += [""]

    # Pareto summary
    greenest = regions[0] if regions else None
    if greenest and len(regions) > 1 and not greenest.error:
        ref_intensity = (
            greenest.best_window_intensity_gco2_kwh
            or greenest.current_intensity_gco2_kwh
        )
        pareto_parts: list[str] = []
        for r in regions[1:]:
            if r.error:
                continue
            r_intensity = (
                r.best_window_intensity_gco2_kwh
                or r.current_intensity_gco2_kwh
            )
            if ref_intensity > 0 and r_intensity > 0:
                pct_diff = (r_intensity - ref_intensity) / r_intensity * 100
                pareto_parts.append(
                    f"**{greenest.display_name}** is {pct_diff:.0f}% greener "
                    f"than {r.display_name}"
                )

        if pareto_parts:
            lines += [
                "### 📊 Pareto Summary",
                "",
            ]
            for p in pareto_parts:
                lines.append(f"- {p}")
            lines += [""]

    # Recommendation
    if greenest and not greenest.error:
        lines += [
            f"> 🏆 **Greenest region: `{greenest.display_name}`** — "
            f"effective intensity: "
            f"`{greenest.best_window_intensity_gco2_kwh or greenest.current_intensity_gco2_kwh:.1f}"
            f" gCO₂e/kWh`",
            "",
        ]

    lines += [
        "*Data sourced from [GSF Carbon Aware SDK]"
        "(https://github.com/Green-Software-Foundation/carbon-aware-sdk) "
        "— generated by [🌱 GreenPipe](https://github.com/greenpipe)*",
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
        "| `@greenpipe optimize` | Analyse MR code for energy efficiency (Claude AI) |\n"
        "| `@greenpipe regions` | Compare carbon intensity across multiple regions |\n"
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
