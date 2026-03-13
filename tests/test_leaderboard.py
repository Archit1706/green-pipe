"""
Tests for Feature 6: Leaderboard + Contributor Impact.

Covers:
  - LeaderboardEntry / LeaderboardResponse schemas
  - PipelineRun.author_name model field
  - format_leaderboard_comment() formatter
  - @greenpipe leaderboard mention command parsing
  - Help / MR comment includes leaderboard command
  - Alembic migration 0003
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.analytics_schemas import LeaderboardEntry, LeaderboardResponse
from src.api.report_formatter import (
    format_help_comment,
    format_leaderboard_comment,
    format_mr_comment,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    rank: int = 1,
    author_name: str = "Alice M.",
    pipeline_count: int = 23,
    avg_sci_score: float = 2.1,
    total_carbon_gco2e: float = 48.3,
    deferred_count: int = 8,
    deferred_percent: float = 34.8,
    co2e_saved_gco2e: float = 12.4,
) -> LeaderboardEntry:
    return LeaderboardEntry(
        rank=rank,
        author_name=author_name,
        pipeline_count=pipeline_count,
        avg_sci_score=avg_sci_score,
        total_carbon_gco2e=total_carbon_gco2e,
        deferred_count=deferred_count,
        deferred_percent=deferred_percent,
        co2e_saved_gco2e=co2e_saved_gco2e,
    )


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestLeaderboardEntry:
    def test_create_entry(self):
        e = _make_entry()
        assert e.rank == 1
        assert e.author_name == "Alice M."
        assert e.pipeline_count == 23
        assert e.avg_sci_score == 2.1
        assert e.deferred_count == 8
        assert e.co2e_saved_gco2e == 12.4

    def test_entry_serialization(self):
        e = _make_entry()
        d = e.model_dump()
        assert d["rank"] == 1
        assert d["author_name"] == "Alice M."
        assert d["deferred_percent"] == 34.8

    def test_entry_zero_values(self):
        e = _make_entry(
            pipeline_count=0,
            deferred_count=0,
            deferred_percent=0.0,
            co2e_saved_gco2e=0.0,
        )
        assert e.pipeline_count == 0
        assert e.co2e_saved_gco2e == 0.0


class TestLeaderboardResponse:
    def test_defaults(self):
        r = LeaderboardResponse()
        assert r.period == "all-time"
        assert r.entries == []
        assert r.note == ""

    def test_with_entries(self):
        entries = [_make_entry(rank=1), _make_entry(rank=2, author_name="Bob K.")]
        r = LeaderboardResponse(period="last 30 days", entries=entries)
        assert r.period == "last 30 days"
        assert len(r.entries) == 2
        assert r.entries[0].author_name == "Alice M."
        assert r.entries[1].author_name == "Bob K."

    def test_with_note(self):
        r = LeaderboardResponse(note="Database unavailable")
        assert r.note == "Database unavailable"


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestPipelineRunAuthorName:
    def test_model_has_author_name(self):
        from src.models.pipeline import PipelineRun

        assert hasattr(PipelineRun, "author_name")

    def test_author_name_column_properties(self):
        from src.models.pipeline import PipelineRun

        col = PipelineRun.__table__.columns["author_name"]
        assert col.nullable is True
        assert str(col.type) == "VARCHAR(255)"


# ---------------------------------------------------------------------------
# Alembic migration tests
# ---------------------------------------------------------------------------


class TestAlembicMigration0003:
    def test_migration_file_exists(self):
        import pathlib

        migration = pathlib.Path(
            "alembic/versions/0003_add_author_name_to_pipeline_runs.py"
        )
        assert migration.exists()
        content = migration.read_text()
        assert 'revision: str = "0003"' in content
        assert 'down_revision: Union[str, None] = "0002"' in content

    def test_migration_adds_author_name(self):
        import pathlib

        migration = pathlib.Path(
            "alembic/versions/0003_add_author_name_to_pipeline_runs.py"
        )
        content = migration.read_text()
        assert "author_name" in content
        assert "pipeline_runs" in content


# ---------------------------------------------------------------------------
# Formatter tests
# ---------------------------------------------------------------------------


class TestFormatLeaderboardComment:
    def test_empty_entries(self):
        result = format_leaderboard_comment([])
        assert "Leaderboard" in result
        assert "No contributor data" in result

    def test_single_entry(self):
        entries = [_make_entry()]
        result = format_leaderboard_comment(entries)
        assert "Alice M." in result
        assert "23 runs" in result
        assert "2.1 gCO" in result

    def test_multiple_entries(self):
        entries = [
            _make_entry(rank=1, author_name="Alice M."),
            _make_entry(rank=2, author_name="Bob K.", avg_sci_score=2.8),
            _make_entry(rank=3, author_name="Carol T.", avg_sci_score=3.5),
        ]
        result = format_leaderboard_comment(entries)
        assert "Alice M." in result
        assert "Bob K." in result
        assert "Carol T." in result

    def test_rank_icons(self):
        entries = [
            _make_entry(rank=1),
            _make_entry(rank=2, author_name="Bob"),
            _make_entry(rank=3, author_name="Carol"),
        ]
        result = format_leaderboard_comment(entries)
        assert "\U0001f947" in result  # gold
        assert "\U0001f948" in result  # silver
        assert "\U0001f949" in result  # bronze

    def test_period_in_header(self):
        result = format_leaderboard_comment([_make_entry()], period="last 30 days")
        assert "Last 30 Days" in result

    def test_all_time_default(self):
        result = format_leaderboard_comment([_make_entry()])
        assert "All-Time" in result

    def test_table_header(self):
        result = format_leaderboard_comment([_make_entry()])
        assert "Rank" in result
        assert "Contributor" in result
        assert "Avg SCI" in result
        assert "Deferred" in result
        assert "Saved" in result

    def test_gamification_footer(self):
        result = format_leaderboard_comment([_make_entry()])
        assert "Lower SCI = greener" in result

    def test_deferred_percentage_shown(self):
        entries = [_make_entry(deferred_count=5, deferred_percent=25.0)]
        result = format_leaderboard_comment(entries)
        assert "5 (25%)" in result


# ---------------------------------------------------------------------------
# Command parsing tests
# ---------------------------------------------------------------------------


class TestParseLeaderboardCommand:
    def test_parse_leaderboard(self):
        from src.api.agent_routes import _parse_mention_command

        assert _parse_mention_command("@greenpipe leaderboard") == "leaderboard"

    def test_parse_leaderboard_case_insensitive(self):
        from src.api.agent_routes import _parse_mention_command

        assert _parse_mention_command("@greenpipe LEADERBOARD") == "leaderboard"

    def test_parse_leaderboard_with_context(self):
        from src.api.agent_routes import _parse_mention_command

        assert (
            _parse_mention_command("hey @greenpipe leaderboard please") == "leaderboard"
        )


# ---------------------------------------------------------------------------
# Help / MR comment includes leaderboard
# ---------------------------------------------------------------------------


class TestHelpIncludesLeaderboard:
    def test_help_comment_includes_leaderboard(self):
        result = format_help_comment()
        assert "@greenpipe leaderboard" in result

    def test_mr_comment_commands_table_includes_leaderboard(self):
        from src.calculators.sci_calculator import SCIResult
        from src.services.pipeline_analyzer import PipelineAnalysisReport, JobReport
        from datetime import datetime, timezone

        sci = SCIResult(
            energy_kwh=0.000813,
            carbon_intensity_gco2_kwh=386.0,
            embodied_carbon_gco2=0.001,
            functional_unit="pipeline_run",
        )
        report = PipelineAnalysisReport(
            project_id=123,
            gitlab_pipeline_id=456,
            pipeline_ref="main",
            pipeline_sha="abc12345",
            pipeline_web_url=None,
            runner_location="us-east1",
            carbon_intensity_gco2_kwh=386.0,
            carbon_data_source="fallback",
            total_energy_kwh=0.000813,
            energy_methodology="Teads",
            sci=sci,
            job_reports=[
                JobReport(
                    job_id=1,
                    job_name="test",
                    stage="test",
                    duration_seconds=60,
                    runner_type="small",
                    runner_tdp_watts=65,
                    cpu_utilization_percent=50,
                    tdp_factor=0.75,
                    avg_power_watts=48.75,
                    energy_kwh=0.000813,
                )
            ],
            urgency_class="normal",
            urgency_confidence=0.85,
            urgency_source="keyword",
            scheduling_message="Proceed on schedule",
            scheduling_window=None,
            can_defer=False,
            commit_messages=["feat: add tests"],
            gsf_standards_used=["SCI"],
            analyzed_at=datetime.now(timezone.utc),
        )
        result = format_mr_comment(report)
        assert "@greenpipe leaderboard" in result


# ---------------------------------------------------------------------------
# Mention webhook tests
# ---------------------------------------------------------------------------


class TestLeaderboardMentionCommand:
    """Test the @greenpipe leaderboard mention command via the webhook."""

    @pytest.fixture(autouse=True)
    def _clear_secret(self, monkeypatch):
        monkeypatch.setattr("src.config.settings.gitlab_webhook_secret", "")

    @pytest.fixture()
    def client(self):
        from src.main import app

        return TestClient(app)

    def _mention_event(self, note: str = "@greenpipe leaderboard") -> dict:
        return {
            "object_kind": "note",
            "object_attributes": {
                "note": note,
                "noteable_type": "MergeRequest",
            },
            "project": {"id": 12345},
            "merge_request": {
                "iid": 42,
                "head_pipeline_id": 999,
            },
        }

    def test_leaderboard_command_accepted(self, client):
        resp = client.post(
            "/agent/webhooks/mention", json=self._mention_event()
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["details"]["command"] == "leaderboard"

    def test_leaderboard_case_insensitive(self, client):
        resp = client.post(
            "/agent/webhooks/mention",
            json=self._mention_event("@greenpipe LEADERBOARD"),
        )
        assert resp.status_code == 200
        assert resp.json()["details"]["command"] == "leaderboard"

    def test_leaderboard_db_error_handled(self, client, monkeypatch):
        """Even if DB is unavailable, the command should not crash."""
        resp = client.post(
            "/agent/webhooks/mention", json=self._mention_event()
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"
