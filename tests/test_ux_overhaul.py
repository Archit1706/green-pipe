"""
Tests for Feature 3: MR Comment UX Overhaul.

Validates the new compact summary card, Developer Impact section,
Available Commands section, and collapsible detail sections in
format_mr_comment().
"""

from __future__ import annotations

import asyncio

import pytest

from src.api.report_formatter import format_mr_comment, format_help_comment
from src.services.pipeline_analyzer import PipelineAnalyzer


# ---------------------------------------------------------------------------
# Shared fixture — minimal PipelineAnalysisReport
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sample_report():
    """Build a minimal PipelineAnalysisReport via the offline analyzer."""
    analyzer = PipelineAnalyzer()

    async def _run():
        return await analyzer.analyze_from_data(
            jobs=[
                {
                    "job_name": "build",
                    "runner_type": "saas-linux-medium-amd64",
                    "duration_seconds": 300,
                    "cpu_utilization_percent": 60.0,
                },
                {
                    "job_name": "test",
                    "runner_type": "saas-linux-small-amd64",
                    "duration_seconds": 120,
                    "cpu_utilization_percent": 40.0,
                },
            ],
            commit_messages=["feat: add dark mode"],
            runner_location="us-east1",
        )

    return asyncio.run(_run())


@pytest.fixture(scope="module")
def deferrable_report():
    """Build a report with a deferrable pipeline."""
    analyzer = PipelineAnalyzer()

    async def _run():
        return await analyzer.analyze_from_data(
            jobs=[
                {
                    "job_name": "lint",
                    "runner_type": "saas-linux-small-amd64",
                    "duration_seconds": 60,
                    "cpu_utilization_percent": 20.0,
                },
            ],
            commit_messages=["docs: update changelog"],
            runner_location="us-east1",
        )

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# 1. Compact Summary Card tests
# ---------------------------------------------------------------------------


class TestCompactSummaryCard:
    """The top of the MR comment should have a one-line summary + scorecard table."""

    def test_header_contains_sci_score(self, sample_report):
        md = format_mr_comment(sample_report)
        sci_score = f"{sample_report.sci.sci_score:.2f} gCO₂e"
        assert sci_score in md

    def test_header_contains_urgency_short(self, sample_report):
        md = format_mr_comment(sample_report)
        # Should have the short-form urgency badge in header
        assert "🟡 Normal" in md or "🟢 Deferrable" in md or "🔴 Urgent" in md

    def test_summary_table_present(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "SCI Score" in md
        assert "Energy" in md
        assert "Carbon Intensity" in md
        assert "Urgency" in md
        assert "Recommended Action" in md

    def test_summary_has_recommended_action(self, sample_report):
        md = format_mr_comment(sample_report)
        # Normal pipeline should say "Proceed on schedule"
        assert "Proceed on schedule" in md or "Defer to" in md or "Run immediately" in md

    def test_pipeline_ref_in_metadata_line(self, sample_report):
        md = format_mr_comment(sample_report)
        # The compact format moves pipeline ref to metadata line
        assert "unknown ref" in md or sample_report.pipeline_ref in md

    def test_deferrable_header_shows_savings(self, deferrable_report):
        md = format_mr_comment(deferrable_report)
        # If there's a scheduling window, the header should mention savings
        if deferrable_report.scheduling_window:
            assert "savings available" in md


# ---------------------------------------------------------------------------
# 2. Collapsible sections tests
# ---------------------------------------------------------------------------


class TestCollapsibleSections:
    """SCI breakdown and per-job table should be in <details> tags."""

    def test_sci_in_details(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "<details>" in md
        assert "SCI (ISO/IEC 21031:2024)" in md
        assert "Full Breakdown" in md

    def test_energy_breakdown_in_details(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "Energy Breakdown" in md
        assert "2 job(s)" in md
        assert "kWh total" in md

    def test_sci_formula_present(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "SCI = ((E × I) + M) / R" in md

    def test_job_names_still_present(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "build" in md
        assert "test" in md


# ---------------------------------------------------------------------------
# 3. Carbon Intensity section (compact)
# ---------------------------------------------------------------------------


class TestCarbonIntensityCompact:
    """Carbon intensity should be in a compact header-style format."""

    def test_location_and_intensity_in_header(self, sample_report):
        md = format_mr_comment(sample_report)
        assert f"{sample_report.carbon_intensity_gco2_kwh:.1f} gCO₂e/kWh" in md
        assert sample_report.runner_location in md

    def test_source_present(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "Source:" in md


# ---------------------------------------------------------------------------
# 4. Developer Impact section
# ---------------------------------------------------------------------------


class TestDeveloperImpact:
    """New Developer Impact section addresses the 'AI Paradox' suggestion."""

    def test_section_header_present(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "Developer Time Saved" in md

    def test_automated_analysis_message(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "automated" in md.lower()
        assert "no manual setup required" in md.lower()

    def test_pipeline_analysis_time_saved(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "Pipeline analysis" in md
        assert "5 min" in md

    def test_scheduling_time_saved(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "Scheduling recommendation" in md
        assert "10 min" in md

    def test_historical_tracking_time_saved(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "Historical tracking" in md
        assert "spreadsheet" in md


# ---------------------------------------------------------------------------
# 5. Available Commands section
# ---------------------------------------------------------------------------


class TestAvailableCommands:
    """Commands should be directly in the MR comment for discoverability."""

    def test_section_header_present(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "Available Commands" in md

    def test_run_now_command_listed(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "@greenpipe run-now" in md

    def test_defer_command_listed(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "@greenpipe defer" in md

    def test_optimize_command_listed(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "@greenpipe optimize" in md

    def test_why_command_listed(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "@greenpipe why" in md

    def test_schedule_command_listed(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "@greenpipe schedule" in md

    def test_help_command_listed(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "@greenpipe help" in md

    def test_commands_in_table_format(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "| Command | Effect |" in md


# ---------------------------------------------------------------------------
# 6. GSF Footer (still present)
# ---------------------------------------------------------------------------


class TestGSFFooter:
    """GSF standards footer should still be present."""

    def test_gsf_standards_present(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "GSF Standards Used" in md

    def test_greenpipe_attribution(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "Green Software Foundation" in md or "greensoftware" in md


# ---------------------------------------------------------------------------
# 7. Help command includes optimize
# ---------------------------------------------------------------------------


class TestHelpCommandUpdated:
    """Verify help text includes all commands from the UX overhaul."""

    def test_help_includes_optimize(self):
        help_md = format_help_comment()
        assert "@greenpipe optimize" in help_md

    def test_help_includes_defer(self):
        help_md = format_help_comment()
        assert "@greenpipe defer" in help_md

    def test_help_includes_why(self):
        help_md = format_help_comment()
        assert "@greenpipe why" in help_md

    def test_help_includes_run_now(self):
        help_md = format_help_comment()
        assert "@greenpipe run-now" in help_md

    def test_help_includes_confirm_defer(self):
        help_md = format_help_comment()
        assert "@greenpipe confirm-defer" in help_md


# ---------------------------------------------------------------------------
# 8. Overall length check — compact is shorter than original
# ---------------------------------------------------------------------------


class TestCompactness:
    """The new format should still produce substantial output."""

    def test_output_is_substantial(self, sample_report):
        """Should still be a rich report."""
        md = format_mr_comment(sample_report)
        assert len(md) > 500

    def test_closing_details_tags_balanced(self, sample_report):
        """Every <details> should have a matching </details>."""
        md = format_mr_comment(sample_report)
        opens = md.count("<details>")
        closes = md.count("</details>")
        assert opens == closes
        assert opens >= 2  # at least SCI and Energy Breakdown

    def test_no_double_hr_separators(self, sample_report):
        """Should not have consecutive --- separators."""
        md = format_mr_comment(sample_report)
        assert "\n---\n\n---\n" not in md
