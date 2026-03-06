"""
Tests for the Green Code Profiler (Feature 2).

Covers:
  - CodeAnalyzer service (mocked Anthropic client)
  - /agent/tools/analyze_code_efficiency endpoint
  - @greenpipe optimize mention command
  - format_code_efficiency_comment() output
  - AnalyzeCodeEfficiencyInput / Output schemas
  - Command regex parsing for 'optimize'
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.agent_schemas import (
    AnalyzeCodeEfficiencyInput,
    AnalyzeCodeEfficiencyOutput,
    CodeEfficiencySuggestion,
)
from src.api.report_formatter import format_code_efficiency_comment, format_help_comment
from src.services.code_analyzer import (
    CodeAnalysisResult,
    CodeAnalyzer,
    EfficiencySuggestion,
    _SYSTEM_PROMPT,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_DIFF = """\
--- a/src/api/routes.py
+++ b/src/api/routes.py
@@ -47,6 +47,12 @@
 def get_users():
-    users = []
-    for uid in user_ids:
-        users.append(db.query(User).get(uid))
+    users = db.query(User).filter(User.id.in_(user_ids)).all()
     return users
"""

SAMPLE_CLAUDE_RESPONSE_JSON = json.dumps({
    "suggestions": [
        {
            "file": "src/api/routes.py",
            "line_range": "47-53",
            "issue_type": "n_plus_one_query",
            "description": "Loop fetches each user individually from the DB.",
            "estimated_energy_impact": "high",
            "suggested_fix": "Use a single query with .filter(User.id.in_(ids)).",
        }
    ],
    "overall_assessment": "The diff fixes an N+1 query pattern. Good improvement.",
    "estimated_energy_reduction": "20-40%",
})


def _make_mock_response(text: str, input_tokens: int = 100, output_tokens: int = 200):
    """Create a mock Anthropic API response."""
    block = SimpleNamespace(text=text)
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    return SimpleNamespace(content=[block], usage=usage)


# ---------------------------------------------------------------------------
# CodeAnalyzer unit tests
# ---------------------------------------------------------------------------


class TestCodeAnalyzerUnavailable:
    """Tests when the analyzer is not configured."""

    def test_no_api_key_returns_error(self, monkeypatch):
        monkeypatch.setattr("src.services.code_analyzer.settings.anthropic_api_key", "")
        analyzer = CodeAnalyzer()
        assert not analyzer.is_available

    @pytest.mark.asyncio
    async def test_analyze_when_unavailable(self, monkeypatch):
        monkeypatch.setattr("src.services.code_analyzer.settings.anthropic_api_key", "")
        analyzer = CodeAnalyzer()
        result = await analyzer.analyze_diff("some diff")
        assert result.error is not None
        assert "unavailable" in result.error.lower() or "not configured" in result.error.lower()
        assert result.suggestions == []


class TestCodeAnalyzerParsing:
    """Tests for response parsing logic."""

    def test_parse_valid_json(self):
        analyzer = CodeAnalyzer.__new__(CodeAnalyzer)
        analyzer._client = None
        analyzer._available = False

        result = analyzer._parse_response(
            SAMPLE_CLAUDE_RESPONSE_JSON, "claude-sonnet-4-6", 300
        )
        assert len(result.suggestions) == 1
        assert result.suggestions[0].issue_type == "n_plus_one_query"
        assert result.suggestions[0].estimated_energy_impact == "high"
        assert result.overall_assessment != ""
        assert result.model_used == "claude-sonnet-4-6"
        assert result.tokens_used == 300
        assert result.error is None

    def test_parse_json_with_markdown_fences(self):
        analyzer = CodeAnalyzer.__new__(CodeAnalyzer)
        analyzer._client = None
        analyzer._available = False

        wrapped = f"```json\n{SAMPLE_CLAUDE_RESPONSE_JSON}\n```"
        result = analyzer._parse_response(wrapped, "claude-sonnet-4-6", 200)
        assert len(result.suggestions) == 1
        assert result.error is None

    def test_parse_invalid_json(self):
        analyzer = CodeAnalyzer.__new__(CodeAnalyzer)
        analyzer._client = None
        analyzer._available = False

        result = analyzer._parse_response("not json at all", "test-model", 50)
        assert result.error is not None
        assert "parse" in result.error.lower()

    def test_parse_empty_suggestions(self):
        analyzer = CodeAnalyzer.__new__(CodeAnalyzer)
        analyzer._client = None
        analyzer._available = False

        data = json.dumps({
            "suggestions": [],
            "overall_assessment": "Code looks efficient.",
            "estimated_energy_reduction": "0%",
        })
        result = analyzer._parse_response(data, "test-model", 100)
        assert result.suggestions == []
        assert "efficient" in result.overall_assessment.lower()
        assert result.error is None


class TestCodeAnalyzerAnalyzeDiff:
    """Tests for the analyze_diff method with mocked Anthropic client."""

    @pytest.mark.asyncio
    async def test_empty_diff_returns_error(self, monkeypatch):
        monkeypatch.setattr("src.services.code_analyzer.settings.anthropic_api_key", "sk-test")
        monkeypatch.setattr("src.services.code_analyzer._ANTHROPIC_AVAILABLE", True)

        analyzer = CodeAnalyzer.__new__(CodeAnalyzer)
        analyzer._client = MagicMock()
        analyzer._available = True

        result = await analyzer.analyze_diff("")
        assert result.error is not None
        assert "empty" in result.error.lower()

    @pytest.mark.asyncio
    async def test_successful_analysis(self, monkeypatch):
        monkeypatch.setattr("src.services.code_analyzer.settings.anthropic_api_key", "sk-test")
        monkeypatch.setattr("src.services.code_analyzer.settings.anthropic_model", "claude-sonnet-4-6")

        analyzer = CodeAnalyzer.__new__(CodeAnalyzer)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_mock_response(
            SAMPLE_CLAUDE_RESPONSE_JSON
        )
        analyzer._client = mock_client
        analyzer._available = True

        result = await analyzer.analyze_diff(SAMPLE_DIFF)
        assert result.error is None
        assert len(result.suggestions) == 1
        assert result.suggestions[0].file == "src/api/routes.py"
        assert result.model_used == "claude-sonnet-4-6"
        assert result.tokens_used == 300

        # Verify the API was called correctly
        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["system"] == _SYSTEM_PROMPT
        assert call_kwargs.kwargs["max_tokens"] == 2048

    @pytest.mark.asyncio
    async def test_api_error_handled_gracefully(self, monkeypatch):
        monkeypatch.setattr("src.services.code_analyzer.settings.anthropic_api_key", "sk-test")
        monkeypatch.setattr("src.services.code_analyzer.settings.anthropic_model", "claude-sonnet-4-6")

        analyzer = CodeAnalyzer.__new__(CodeAnalyzer)
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API rate limit exceeded")
        analyzer._client = mock_client
        analyzer._available = True

        result = await analyzer.analyze_diff(SAMPLE_DIFF)
        assert result.error is not None
        assert "rate limit" in result.error.lower()
        assert result.suggestions == []

    @pytest.mark.asyncio
    async def test_large_diff_truncated(self, monkeypatch):
        monkeypatch.setattr("src.services.code_analyzer.settings.anthropic_api_key", "sk-test")
        monkeypatch.setattr("src.services.code_analyzer.settings.anthropic_model", "claude-sonnet-4-6")

        analyzer = CodeAnalyzer.__new__(CodeAnalyzer)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_mock_response(
            json.dumps({"suggestions": [], "overall_assessment": "OK", "estimated_energy_reduction": "0%"})
        )
        analyzer._client = mock_client
        analyzer._available = True

        # Create a diff larger than 30k chars
        large_diff = "x" * 50_000
        result = await analyzer.analyze_diff(large_diff)
        assert result.error is None

        # Verify the diff sent to Claude was truncated
        call_kwargs = mock_client.messages.create.call_args
        content = call_kwargs.kwargs["messages"][0]["content"]
        assert "truncated" in content.lower()


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestCodeEfficiencySchemas:
    """Tests for input/output Pydantic schemas."""

    def test_input_with_project_and_mr(self):
        inp = AnalyzeCodeEfficiencyInput(project_id=123, mr_iid=42)
        assert inp.project_id == 123
        assert inp.mr_iid == 42
        assert inp.diff_text is None

    def test_input_with_diff_text(self):
        inp = AnalyzeCodeEfficiencyInput(diff_text="--- a/foo\n+++ b/foo")
        assert inp.diff_text is not None
        assert inp.project_id is None

    def test_output_with_suggestions(self):
        out = AnalyzeCodeEfficiencyOutput(
            suggestions=[
                CodeEfficiencySuggestion(
                    file="test.py",
                    line_range="1-5",
                    issue_type="missing_cache",
                    description="No caching",
                    estimated_energy_impact="medium",
                    suggested_fix="Add cache",
                )
            ],
            overall_assessment="Needs work",
            estimated_energy_reduction="15-25%",
            model_used="claude-sonnet-4-6",
            tokens_used=500,
            diff_source="gitlab_mr",
        )
        assert len(out.suggestions) == 1
        assert out.suggestions[0].issue_type == "missing_cache"
        assert out.diff_source == "gitlab_mr"

    def test_output_error_only(self):
        out = AnalyzeCodeEfficiencyOutput(
            error="Not configured",
            diff_source="unavailable",
        )
        assert out.error is not None
        assert out.suggestions == []


# ---------------------------------------------------------------------------
# Report formatter tests
# ---------------------------------------------------------------------------


class TestFormatCodeEfficiencyComment:
    """Tests for the code efficiency MR comment formatter."""

    def test_with_suggestions(self):
        result = CodeAnalysisResult(
            suggestions=[
                EfficiencySuggestion(
                    file="src/api/routes.py",
                    line_range="47-53",
                    issue_type="n_plus_one_query",
                    description="Loop fetches each user individually.",
                    estimated_energy_impact="high",
                    suggested_fix="Use batch query.",
                ),
                EfficiencySuggestion(
                    file="src/utils.py",
                    line_range="10-12",
                    issue_type="missing_cache",
                    description="Expensive call repeated.",
                    estimated_energy_impact="medium",
                    suggested_fix="Add cachetools.TTLCache.",
                ),
            ],
            overall_assessment="Two efficiency issues found.",
            estimated_energy_reduction="15-30%",
            model_used="claude-sonnet-4-6",
            tokens_used=450,
        )
        md = format_code_efficiency_comment(result)
        assert "GreenPipe Code Efficiency Analysis" in md
        assert "2 suggestion(s)" in md
        assert "N Plus One Query" in md
        assert "Missing Cache" in md
        assert "src/api/routes.py" in md
        assert "15-30%" in md
        assert "claude-sonnet-4-6" in md

    def test_no_suggestions(self):
        result = CodeAnalysisResult(
            suggestions=[],
            overall_assessment="Code looks efficient.",
            model_used="claude-sonnet-4-6",
            tokens_used=100,
        )
        md = format_code_efficiency_comment(result)
        assert "No energy inefficiencies detected" in md
        assert "efficient" in md.lower()

    def test_error_result(self):
        result = CodeAnalysisResult(
            error="ANTHROPIC_API_KEY not set",
        )
        md = format_code_efficiency_comment(result)
        assert "ANTHROPIC_API_KEY" in md

    def test_impact_icons_present(self):
        result = CodeAnalysisResult(
            suggestions=[
                EfficiencySuggestion(
                    file="a.py", line_range="1", issue_type="test",
                    description="high impact", estimated_energy_impact="high",
                    suggested_fix="fix",
                ),
                EfficiencySuggestion(
                    file="b.py", line_range="2", issue_type="test",
                    description="medium impact", estimated_energy_impact="medium",
                    suggested_fix="fix",
                ),
                EfficiencySuggestion(
                    file="c.py", line_range="3", issue_type="test",
                    description="low impact", estimated_energy_impact="low",
                    suggested_fix="fix",
                ),
            ],
            model_used="test",
        )
        md = format_code_efficiency_comment(result)
        # Check impact icons are used correctly
        assert "\U0001f534" in md  # 🔴 high
        assert "\U0001f7e1" in md  # 🟡 medium
        assert "\U0001f7e2" in md  # 🟢 low


# ---------------------------------------------------------------------------
# Help comment includes optimize
# ---------------------------------------------------------------------------


class TestHelpIncludesOptimize:
    """Verify the help text includes the optimize command."""

    def test_help_lists_optimize(self):
        help_text = format_help_comment()
        assert "optimize" in help_text.lower()

    def test_help_mentions_claude(self):
        help_text = format_help_comment()
        assert "Claude" in help_text or "claude" in help_text.lower()


# ---------------------------------------------------------------------------
# Command parsing for 'optimize'
# ---------------------------------------------------------------------------


class TestParseOptimizeCommand:
    """Verify the command regex recognises 'optimize'."""

    @pytest.mark.parametrize("note,expected", [
        ("@greenpipe optimize", "optimize"),
        ("@greenpipe OPTIMIZE this MR please", "optimize"),
        ("Hey @greenpipe optimize", "optimize"),
    ])
    def test_optimize_parsed(self, note, expected):
        from src.api.agent_routes import _parse_mention_command
        assert _parse_mention_command(note) == expected


# ---------------------------------------------------------------------------
# Endpoint tests (FastAPI TestClient)
# ---------------------------------------------------------------------------


class TestAnalyzeCodeEfficiencyEndpoint:
    """Tests for POST /agent/tools/analyze_code_efficiency."""

    @pytest.fixture
    def client(self):
        from src.api.agent_routes import agent_tools_router
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(agent_tools_router)
        return TestClient(app)

    def test_unavailable_returns_error(self, client, monkeypatch):
        monkeypatch.setattr(
            "src.api.agent_routes._code_analyzer",
            type("FakeAnalyzer", (), {"is_available": False})(),
        )
        resp = client.post(
            "/agent/tools/analyze_code_efficiency",
            json={"diff_text": "some diff"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] is not None
        assert data["diff_source"] == "unavailable"

    def test_no_diff_provided_returns_error(self, client, monkeypatch):
        monkeypatch.setattr(
            "src.api.agent_routes._code_analyzer",
            type("FakeAnalyzer", (), {"is_available": True})(),
        )
        monkeypatch.setattr("src.api.agent_routes._analyzer._gitlab", None)
        resp = client.post(
            "/agent/tools/analyze_code_efficiency",
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] is not None
        assert "no diff" in data["error"].lower()

    def test_manual_diff_analysis(self, client, monkeypatch):
        mock_result = CodeAnalysisResult(
            suggestions=[
                EfficiencySuggestion(
                    file="test.py",
                    line_range="1-5",
                    issue_type="test_issue",
                    description="Test",
                    estimated_energy_impact="low",
                    suggested_fix="Fix it",
                )
            ],
            overall_assessment="Good",
            estimated_energy_reduction="5%",
            model_used="claude-sonnet-4-6",
            tokens_used=200,
        )

        fake_analyzer = MagicMock()
        fake_analyzer.is_available = True
        fake_analyzer.analyze_diff = AsyncMock(return_value=mock_result)

        monkeypatch.setattr("src.api.agent_routes._code_analyzer", fake_analyzer)

        resp = client.post(
            "/agent/tools/analyze_code_efficiency",
            json={"diff_text": SAMPLE_DIFF},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] is None
        assert len(data["suggestions"]) == 1
        assert data["diff_source"] == "manual"
        assert data["model_used"] == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Mention webhook optimize command
# ---------------------------------------------------------------------------


class TestMentionOptimizeCommand:
    """Tests for @greenpipe optimize via the mention webhook."""

    @pytest.fixture
    def client(self):
        from src.api.agent_routes import webhook_router
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(webhook_router)
        return TestClient(app)

    def test_optimize_without_code_analyzer(self, client, monkeypatch):
        """When code analyzer is unavailable, returns a skipped response."""
        fake_analyzer = MagicMock()
        fake_analyzer.is_available = False
        monkeypatch.setattr("src.api.agent_routes._code_analyzer", fake_analyzer)
        monkeypatch.setattr("src.api.agent_routes.settings.gitlab_webhook_secret", "")

        resp = client.post(
            "/agent/webhooks/mention",
            json={
                "object_kind": "note",
                "user": {"username": "dev"},
                "project": {"id": 1},
                "object_attributes": {
                    "note": "@greenpipe optimize",
                    "noteable_type": "MergeRequest",
                },
                "merge_request": {"iid": 10, "source_branch": "feature/x"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "skipped"
        assert "not configured" in data["message"].lower()

    def test_optimize_without_diff(self, client, monkeypatch):
        """When MR diff can't be fetched, returns error."""
        fake_code_analyzer = MagicMock()
        fake_code_analyzer.is_available = True
        monkeypatch.setattr("src.api.agent_routes._code_analyzer", fake_code_analyzer)
        monkeypatch.setattr("src.api.agent_routes.settings.gitlab_webhook_secret", "")

        fake_gitlab = MagicMock()
        fake_gitlab.get_mr_diff.return_value = None
        fake_gitlab.post_mr_comment.return_value = True
        monkeypatch.setattr("src.api.agent_routes._analyzer._gitlab", fake_gitlab)

        resp = client.post(
            "/agent/webhooks/mention",
            json={
                "object_kind": "note",
                "user": {"username": "dev"},
                "project": {"id": 1},
                "object_attributes": {
                    "note": "@greenpipe optimize",
                    "noteable_type": "MergeRequest",
                },
                "merge_request": {"iid": 10, "source_branch": "feature/x"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert "diff" in data["message"].lower()


# ---------------------------------------------------------------------------
# System prompt sanity checks
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    """Verify the system prompt has the right structure."""

    def test_prompt_mentions_green_software(self):
        assert "green software" in _SYSTEM_PROMPT.lower()

    def test_prompt_mentions_json(self):
        assert "JSON" in _SYSTEM_PROMPT

    def test_prompt_mentions_key_issue_types(self):
        for issue in ["N+1", "caching", "unbounded", "async"]:
            assert issue.lower() in _SYSTEM_PROMPT.lower()
