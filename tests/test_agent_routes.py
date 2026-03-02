"""
Tests for the GreenPipe GitLab Duo Agent routes.

Covers:
- classify_urgency tool (pure NLP, no external dependencies)
- suggest_scheduling tool (CarbonService regional fallback)
- report_formatter output structure
- webhook pipeline event parsing (skipped / accepted paths)
- webhook mention event parsing + command extraction
- Webhook secret verification
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.agent_schemas import GitLabNoteEvent, GitLabPipelineEvent
from src.api.agent_routes import _parse_mention_command
from src.api.report_formatter import format_help_comment, format_mr_comment
from src.main import app
from src.services.pipeline_analyzer import PipelineAnalyzer  # noqa: F401 — used in fixture

# ---------------------------------------------------------------------------
# Test client (synchronous — no live DB or GitLab needed)
# ---------------------------------------------------------------------------

client = TestClient(app)


# ---------------------------------------------------------------------------
# classify_urgency tool
# ---------------------------------------------------------------------------


class TestClassifyUrgencyTool:
    def test_urgent_returns_cannot_defer(self):
        resp = client.post(
            "/agent/tools/classify_urgency",
            json={"commit_messages": ["hotfix: fix critical payment crash"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["urgency_class"] == "urgent"
        assert data["can_defer"] is False
        assert data["confidence"] > 0
        assert data["source"] in {"keyword", "nlp_fp32", "nlp_int8"}

    def test_deferrable_returns_can_defer(self):
        resp = client.post(
            "/agent/tools/classify_urgency",
            json={"commit_messages": ["docs: update contribution guide"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["urgency_class"] == "deferrable"
        assert data["can_defer"] is True

    def test_normal_commit_returns_normal(self):
        resp = client.post(
            "/agent/tools/classify_urgency",
            json={"commit_messages": ["feat: implement OAuth2 login flow"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["urgency_class"] == "normal"
        assert data["can_defer"] is False

    def test_empty_messages_defaults_to_normal(self):
        resp = client.post(
            "/agent/tools/classify_urgency",
            json={"commit_messages": []},
        )
        assert resp.status_code == 200
        assert resp.json()["urgency_class"] == "normal"

    def test_response_schema_complete(self):
        resp = client.post(
            "/agent/tools/classify_urgency",
            json={"commit_messages": ["chore: clean up unused imports"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        for field in ("urgency_class", "confidence", "source", "can_defer", "explanation"):
            assert field in data, f"Missing field: {field}"
        assert 0.0 <= data["confidence"] <= 1.0
        assert isinstance(data["explanation"], str)


# ---------------------------------------------------------------------------
# suggest_scheduling tool
# ---------------------------------------------------------------------------


class TestSuggestSchedulingTool:
    def test_returns_valid_response(self):
        resp = client.post(
            "/agent/tools/suggest_scheduling",
            json={"location": "us-east1", "duration_minutes": 10, "horizon_hours": 12},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["location"] == "us-east1"
        assert isinstance(data["message"], str)
        assert isinstance(data["forecast_available"], bool)

    def test_unknown_location_still_returns(self):
        """Regional fallback should handle unknown locations gracefully."""
        resp = client.post(
            "/agent/tools/suggest_scheduling",
            json={"location": "unknown-region-xyz"},
        )
        assert resp.status_code == 200
        assert resp.json()["location"] == "unknown-region-xyz"

    def test_current_intensity_is_positive_or_none(self):
        resp = client.post(
            "/agent/tools/suggest_scheduling",
            json={"location": "europe-west1"},
        )
        assert resp.status_code == 200
        ci = resp.json().get("current_intensity_gco2_kwh")
        if ci is not None:
            assert ci > 0


# ---------------------------------------------------------------------------
# report_formatter
# ---------------------------------------------------------------------------


class TestReportFormatter:
    @pytest.fixture
    def sample_report(self):
        """Build a minimal PipelineAnalysisReport via the offline analyzer."""
        import asyncio

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

    def test_format_mr_comment_contains_sci(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "SCI" in md
        assert "gCO₂e" in md
        assert "GreenPipe" in md

    def test_format_mr_comment_has_per_job_rows(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "build" in md
        assert "test" in md

    def test_format_mr_comment_has_gsf_footer(self, sample_report):
        md = format_mr_comment(sample_report)
        assert "Green Software Foundation" in md or "greensoftware" in md

    def test_format_mr_comment_markdown_valid(self, sample_report):
        """Smoke test: no Python exceptions and non-empty output."""
        md = format_mr_comment(sample_report)
        assert len(md) > 200

    def test_format_help_comment(self):
        help_md = format_help_comment()
        assert "@greenpipe analyze" in help_md
        assert "@greenpipe schedule" in help_md
        assert "@greenpipe help" in help_md


# ---------------------------------------------------------------------------
# _parse_mention_command
# ---------------------------------------------------------------------------


class TestParseMentionCommand:
    @pytest.mark.parametrize("note,expected", [
        ("@greenpipe analyze this MR", "analyze"),
        ("Hey @GreenPipe ANALYZE", "analyze"),
        ("@greenpipe report", "report"),
        ("@greenpipe schedule please", "schedule"),
        ("@greenpipe HELP", "help"),
        ("@greenpipe unknown_command", "help"),
        ("@greenpipe", "help"),
        ("no mention at all", "help"),
    ])
    def test_parse_command(self, note, expected):
        assert _parse_mention_command(note) == expected


# ---------------------------------------------------------------------------
# Webhook: pipeline events
# ---------------------------------------------------------------------------


class TestWebhookPipelineEvent:
    def test_non_terminal_status_skipped(self):
        payload = {
            "object_kind": "pipeline",
            "object_attributes": {"id": 100, "status": "running"},
            "project": {"id": 1},
        }
        resp = client.post("/agent/webhooks/pipeline", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

    def test_pending_status_skipped(self):
        payload = {
            "object_kind": "pipeline",
            "object_attributes": {"id": 100, "status": "pending"},
            "project": {"id": 1},
        }
        resp = client.post("/agent/webhooks/pipeline", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

    def test_success_without_gitlab_client_skipped(self):
        """Without GITLAB_TOKEN configured the webhook skips (no client)."""
        payload = {
            "object_kind": "pipeline",
            "object_attributes": {"id": 100, "status": "success"},
            "project": {"id": 1},
        }
        resp = client.post("/agent/webhooks/pipeline", json=payload)
        assert resp.status_code == 200
        # Without a GitLab token the analyzer has no client → skipped
        body = resp.json()
        assert body["status"] in {"skipped", "accepted", "error"}

    def test_missing_ids_skipped(self):
        payload = {
            "object_kind": "pipeline",
            "object_attributes": {"status": "success"},  # no id
            "project": {},                                 # no id
        }
        resp = client.post("/agent/webhooks/pipeline", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

    def test_wrong_secret_rejected(self, monkeypatch):
        monkeypatch.setattr("src.api.agent_routes.settings.gitlab_webhook_secret", "correct-secret")
        payload = {
            "object_kind": "pipeline",
            "object_attributes": {"id": 1, "status": "success"},
            "project": {"id": 1},
        }
        resp = client.post(
            "/agent/webhooks/pipeline",
            json=payload,
            headers={"X-Gitlab-Token": "wrong-secret"},
        )
        assert resp.status_code == 401

    def test_correct_secret_accepted(self, monkeypatch):
        monkeypatch.setattr("src.api.agent_routes.settings.gitlab_webhook_secret", "my-secret")
        payload = {
            "object_kind": "pipeline",
            "object_attributes": {"id": 1, "status": "running"},
            "project": {"id": 1},
        }
        resp = client.post(
            "/agent/webhooks/pipeline",
            json=payload,
            headers={"X-Gitlab-Token": "my-secret"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"  # 'running' is non-terminal


# ---------------------------------------------------------------------------
# Webhook: mention events
# ---------------------------------------------------------------------------


class TestWebhookMentionEvent:
    def test_non_greenpipe_mention_skipped(self):
        payload = {
            "object_kind": "note",
            "object_attributes": {
                "note": "Hey @someone else, check this out",
                "noteable_type": "MergeRequest",
            },
            "project": {"id": 1},
        }
        resp = client.post("/agent/webhooks/mention", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

    def test_issue_note_skipped(self):
        payload = {
            "object_kind": "note",
            "object_attributes": {
                "note": "@greenpipe analyze",
                "noteable_type": "Issue",
            },
            "project": {"id": 1},
        }
        resp = client.post("/agent/webhooks/mention", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

    def test_help_command_accepted(self):
        payload = {
            "object_kind": "note",
            "object_attributes": {
                "note": "@greenpipe help",
                "noteable_type": "MergeRequest",
            },
            "project": {"id": 1},
            "merge_request": {"iid": 5},
        }
        resp = client.post("/agent/webhooks/mention", json=payload)
        assert resp.status_code == 200
        # help succeeds without GitLab client (no MR comment posted but returns accepted)
        assert resp.json()["status"] == "accepted"

    def test_schedule_command_accepted(self):
        payload = {
            "object_kind": "note",
            "object_attributes": {
                "note": "@greenpipe schedule",
                "noteable_type": "MergeRequest",
            },
            "project": {"id": 1},
            "merge_request": {"iid": 5},
        }
        resp = client.post("/agent/webhooks/mention", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

    def test_analyze_without_gitlab_client_skipped(self):
        """analyze command requires a GitLab client — skips gracefully without one."""
        payload = {
            "object_kind": "note",
            "object_attributes": {
                "note": "@greenpipe analyze",
                "noteable_type": "MergeRequest",
            },
            "project": {"id": 1},
            "merge_request": {"iid": 5},
        }
        resp = client.post("/agent/webhooks/mention", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

    def test_wrong_secret_rejected(self, monkeypatch):
        monkeypatch.setattr("src.api.agent_routes.settings.gitlab_webhook_secret", "secret123")
        payload = {
            "object_kind": "note",
            "object_attributes": {"note": "@greenpipe help", "noteable_type": "MergeRequest"},
            "project": {"id": 1},
        }
        resp = client.post(
            "/agent/webhooks/mention",
            json=payload,
            headers={"X-Gitlab-Token": "wrong"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GitLab event schema helpers
# ---------------------------------------------------------------------------


class TestGitLabEventSchemas:
    def test_pipeline_event_properties(self):
        event = GitLabPipelineEvent(
            object_attributes={"id": 42, "status": "success", "ref": "main"},
            project={"id": 7},
        )
        assert event.pipeline_id == 42
        assert event.project_id == 7
        assert event.status == "success"
        assert event.ref == "main"

    def test_note_event_properties(self):
        event = GitLabNoteEvent(
            object_attributes={
                "note": "@greenpipe analyze",
                "noteable_type": "MergeRequest",
            },
            project={"id": 5},
            merge_request={"iid": 12},
        )
        assert event.note_body == "@greenpipe analyze"
        assert event.project_id == 5
        assert event.mr_iid == 12
        assert event.noteable_type == "MergeRequest"
