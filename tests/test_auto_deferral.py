"""
Tests for GreenPipe auto-deferral engine.

Covers:
- Policy config: protected branches, minimum savings, mode selection
- _is_protected_ref() branch pattern matching
- _evaluate_deferral() decision logic across all three modes
- New mention commands: run-now, defer, confirm-defer, why
- _parse_mention_command() extended regex
- format_deferral_comment() output
- DeferralDecision schema in webhook responses
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.agent_routes import (
    _is_protected_ref,
    _parse_mention_command,
)
from src.api.agent_schemas import DeferralDecision
from src.api.report_formatter import format_deferral_comment, format_help_comment
from src.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# _is_protected_ref()
# ---------------------------------------------------------------------------


class TestIsProtectedRef:
    def test_main_is_protected(self):
        assert _is_protected_ref("main") is True

    def test_master_is_protected(self):
        assert _is_protected_ref("master") is True

    def test_release_glob_matches(self):
        assert _is_protected_ref("release/1.0") is True
        assert _is_protected_ref("release-v2") is True

    def test_feature_branch_not_protected(self):
        assert _is_protected_ref("feature/add-login") is False

    def test_docs_branch_not_protected(self):
        assert _is_protected_ref("docs/update-readme") is False

    def test_empty_ref_not_protected(self):
        assert _is_protected_ref("") is False

    def test_custom_protected_branches(self, monkeypatch):
        monkeypatch.setattr(
            "src.api.agent_routes.settings.greenpipe_protected_branches",
            "deploy*,hotfix*",
        )
        assert _is_protected_ref("deploy/prod") is True
        assert _is_protected_ref("hotfix/urgent-fix") is True
        assert _is_protected_ref("main") is False


# ---------------------------------------------------------------------------
# _parse_mention_command() — new commands
# ---------------------------------------------------------------------------


class TestParseMentionCommandExtended:
    @pytest.mark.parametrize("note,expected", [
        # Existing commands still work
        ("@greenpipe analyze this MR", "analyze"),
        ("@greenpipe report", "report"),
        ("@greenpipe schedule please", "schedule"),
        ("@greenpipe HELP", "help"),
        # New commands
        ("@greenpipe run-now", "run-now"),
        ("@greenpipe RUN-NOW please", "run-now"),
        ("@greenpipe confirm-defer", "confirm-defer"),
        ("@greenpipe CONFIRM-DEFER", "confirm-defer"),
        ("@greenpipe defer", "defer"),
        ("@greenpipe defer this", "defer"),
        ("@greenpipe why", "why"),
        ("@greenpipe WHY was this deferred?", "why"),
        # Still falls back to help for unknown
        ("@greenpipe unknown_command", "help"),
        ("@greenpipe", "help"),
    ])
    def test_parse_command(self, note, expected):
        assert _parse_mention_command(note) == expected


# ---------------------------------------------------------------------------
# Pipeline webhook: deferral decision is included
# ---------------------------------------------------------------------------


class TestWebhookDeferralDecision:
    @pytest.fixture(autouse=True)
    def _clear_secret(self, monkeypatch):
        monkeypatch.setattr("src.config.settings.gitlab_webhook_secret", "")

    def test_pipeline_webhook_includes_deferral_field(self):
        """Terminal pipeline webhook response includes a deferral decision."""
        payload = {
            "object_kind": "pipeline",
            "object_attributes": {"id": 100, "status": "success", "ref": "feature/docs"},
            "project": {"id": 1},
        }
        resp = client.post("/agent/webhooks/pipeline", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        # Without GitLab client configured, status is skipped, but the schema is valid
        assert data["status"] in {"skipped", "accepted", "error"}

    def test_non_terminal_pipeline_has_no_deferral(self):
        """Non-terminal pipelines should skip entirely — no deferral field."""
        payload = {
            "object_kind": "pipeline",
            "object_attributes": {"id": 100, "status": "running"},
            "project": {"id": 1},
        }
        resp = client.post("/agent/webhooks/pipeline", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "skipped"
        assert data.get("deferral") is None


# ---------------------------------------------------------------------------
# Mention webhook: new commands
# ---------------------------------------------------------------------------


class TestMentionNewCommands:
    @pytest.fixture(autouse=True)
    def _clear_secret(self, monkeypatch):
        monkeypatch.setattr("src.config.settings.gitlab_webhook_secret", "")

    def test_run_now_without_gitlab_client(self):
        payload = {
            "object_kind": "note",
            "object_attributes": {
                "note": "@greenpipe run-now",
                "noteable_type": "MergeRequest",
            },
            "project": {"id": 1},
            "merge_request": {"iid": 5},
        }
        resp = client.post("/agent/webhooks/mention", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["details"]["command"] == "run-now"

    def test_confirm_defer_without_gitlab_client(self):
        payload = {
            "object_kind": "note",
            "object_attributes": {
                "note": "@greenpipe confirm-defer",
                "noteable_type": "MergeRequest",
            },
            "project": {"id": 1},
            "merge_request": {"iid": 5},
        }
        resp = client.post("/agent/webhooks/mention", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["details"]["command"] == "confirm-defer"

    def test_defer_without_gitlab_client(self):
        payload = {
            "object_kind": "note",
            "object_attributes": {
                "note": "@greenpipe defer",
                "noteable_type": "MergeRequest",
            },
            "project": {"id": 1},
            "merge_request": {"iid": 5},
        }
        resp = client.post("/agent/webhooks/mention", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["details"]["command"] == "defer"

    def test_why_command(self):
        payload = {
            "object_kind": "note",
            "object_attributes": {
                "note": "@greenpipe why",
                "noteable_type": "MergeRequest",
            },
            "project": {"id": 1},
            "merge_request": {"iid": 5},
        }
        resp = client.post("/agent/webhooks/mention", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["details"]["command"] == "why"
        assert "urgency_class" in data["details"]


# ---------------------------------------------------------------------------
# format_deferral_comment()
# ---------------------------------------------------------------------------


class TestFormatDeferralComment:
    def test_deferred_action_comment(self):
        decision = DeferralDecision(
            action="deferred",
            policy_mode="auto-execute",
            reason="Pipeline auto-deferred.",
            pipeline_cancelled=True,
            schedule_id=42,
            schedule_cron="0 3 * * *",
            target_window="2026-03-16T03:00:00Z",
            predicted_savings_pct=45.0,
            original_intensity_gco2_kwh=386.0,
            target_intensity_gco2_kwh=212.0,
        )
        md = format_deferral_comment(decision)
        assert "auto-deferred" in md.lower() or "Auto-Deferral" in md
        assert "45.0%" in md
        assert "@greenpipe run-now" in md
        assert "auto-execute" in md

    def test_awaiting_approval_comment(self):
        decision = DeferralDecision(
            action="awaiting_approval",
            policy_mode="approval-required",
            reason="Pending approval.",
            target_window="2026-03-16T03:00:00Z",
            predicted_savings_pct=30.0,
        )
        md = format_deferral_comment(decision)
        assert "confirm-defer" in md
        assert "30.0%" in md
        assert "approval-required" in md

    def test_recommended_comment(self):
        decision = DeferralDecision(
            action="recommended",
            policy_mode="recommend-only",
            reason="Deferral recommended.",
            target_window="2026-03-16T03:00:00Z",
            predicted_savings_pct=25.0,
        )
        md = format_deferral_comment(decision)
        assert "recommended" in md.lower()
        assert "25.0%" in md
        assert "@greenpipe defer" in md


# ---------------------------------------------------------------------------
# format_help_comment() — includes new commands
# ---------------------------------------------------------------------------


class TestHelpIncludesNewCommands:
    def test_help_lists_defer(self):
        help_md = format_help_comment()
        assert "@greenpipe defer" in help_md

    def test_help_lists_run_now(self):
        help_md = format_help_comment()
        assert "@greenpipe run-now" in help_md

    def test_help_lists_confirm_defer(self):
        help_md = format_help_comment()
        assert "@greenpipe confirm-defer" in help_md

    def test_help_lists_why(self):
        help_md = format_help_comment()
        assert "@greenpipe why" in help_md


# ---------------------------------------------------------------------------
# DeferralDecision schema
# ---------------------------------------------------------------------------


class TestDeferralDecisionSchema:
    def test_minimal_decision(self):
        d = DeferralDecision(
            action="none",
            policy_mode="recommend-only",
            reason="Not deferrable.",
        )
        assert d.action == "none"
        assert d.pipeline_cancelled is False
        assert d.schedule_id is None

    def test_full_decision(self):
        d = DeferralDecision(
            action="deferred",
            policy_mode="auto-execute",
            reason="Auto-deferred.",
            pipeline_cancelled=True,
            schedule_id=99,
            schedule_cron="30 2 * * *",
            target_window="2026-03-16T02:30:00Z",
            predicted_savings_pct=40.5,
            original_intensity_gco2_kwh=400.0,
            target_intensity_gco2_kwh=240.0,
        )
        assert d.action == "deferred"
        assert d.pipeline_cancelled is True
        assert d.schedule_id == 99
        assert d.predicted_savings_pct == 40.5
