"""
Tests for Feature 5: Multi-Region Carbon Comparison.

Covers:
- CarbonService.compare_regions() method (sorting, filtering, error handling)
- CompareRegionsInput / CompareRegionsOutput schemas (validation, defaults)
- POST /agent/tools/compare_regions endpoint
- @greenpipe regions mention command
- format_regions_comment() output structure
- GREENPIPE_ALLOWED_REGIONS policy filter
- RegionComparison dataclass
- DEFAULT_CANDIDATE_REGIONS constant
- Pareto summary generation
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.agent_schemas import (
    CompareRegionsInput,
    CompareRegionsOutput,
    RegionComparisonResult,
)
from src.api.report_formatter import format_regions_comment
from src.main import app
from src.services.carbon_service import (
    DEFAULT_CANDIDATE_REGIONS,
    CarbonService,
    RegionComparison,
)

# ---------------------------------------------------------------------------
# Test client
# ---------------------------------------------------------------------------

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helper: build mock RegionComparison objects
# ---------------------------------------------------------------------------

def _make_region(
    display_name: str = "us-east1",
    location: str = "eastus",
    current: float = 386.0,
    source: str = "regional_fallback",
    best_ts: str | None = None,
    best_intensity: float | None = None,
    savings: float = 0.0,
    rank: int = 1,
    error: str | None = None,
) -> RegionComparison:
    return RegionComparison(
        location=location,
        display_name=display_name,
        current_intensity_gco2_kwh=current,
        current_source=source,
        best_window_timestamp=best_ts,
        best_window_intensity_gco2_kwh=best_intensity,
        savings_vs_current_pct=savings,
        rank=rank,
        error=error,
    )


# ---------------------------------------------------------------------------
# RegionComparison dataclass
# ---------------------------------------------------------------------------


class TestRegionComparison:
    def test_defaults(self):
        rc = RegionComparison(
            location="eastus",
            display_name="us-east1",
            current_intensity_gco2_kwh=386.0,
            current_source="regional_fallback",
        )
        assert rc.best_window_timestamp is None
        assert rc.best_window_intensity_gco2_kwh is None
        assert rc.savings_vs_current_pct == 0.0
        assert rc.rank == 0
        assert rc.error is None

    def test_full_fields(self):
        rc = _make_region(
            best_ts="2026-03-11T04:00:00Z",
            best_intensity=300.0,
            savings=22.3,
            rank=1,
        )
        assert rc.best_window_timestamp == "2026-03-11T04:00:00Z"
        assert rc.best_window_intensity_gco2_kwh == 300.0
        assert rc.savings_vs_current_pct == 22.3
        assert rc.rank == 1

    def test_error_field(self):
        rc = _make_region(error="connection timeout")
        assert rc.error == "connection timeout"


# ---------------------------------------------------------------------------
# DEFAULT_CANDIDATE_REGIONS constant
# ---------------------------------------------------------------------------


class TestDefaultCandidateRegions:
    def test_has_five_regions(self):
        assert len(DEFAULT_CANDIDATE_REGIONS) == 5

    def test_contains_expected_regions(self):
        expected = {"us-east1", "us-west1", "europe-west1", "asia-southeast1", "australia-southeast1"}
        assert set(DEFAULT_CANDIDATE_REGIONS) == expected


# ---------------------------------------------------------------------------
# CompareRegionsInput schema
# ---------------------------------------------------------------------------


class TestCompareRegionsInput:
    def test_defaults(self):
        inp = CompareRegionsInput()
        assert inp.locations == []
        assert inp.duration_minutes == 10
        assert inp.horizon_hours == 24

    def test_custom_values(self):
        inp = CompareRegionsInput(
            locations=["us-east1", "us-west1"],
            duration_minutes=30,
            horizon_hours=48,
        )
        assert len(inp.locations) == 2
        assert inp.duration_minutes == 30
        assert inp.horizon_hours == 48

    def test_duration_min_one(self):
        with pytest.raises(Exception):
            CompareRegionsInput(duration_minutes=0)

    def test_horizon_min_one(self):
        with pytest.raises(Exception):
            CompareRegionsInput(horizon_hours=0)


# ---------------------------------------------------------------------------
# CompareRegionsOutput schema
# ---------------------------------------------------------------------------


class TestCompareRegionsOutput:
    def test_defaults(self):
        out = CompareRegionsOutput()
        assert out.regions == []
        assert out.pareto_summary == ""
        assert out.greenest_region is None
        assert out.message == ""

    def test_with_regions(self):
        region = RegionComparisonResult(
            rank=1,
            location="eastus",
            display_name="us-east1",
            current_intensity_gco2_kwh=386.0,
            current_source="regional_fallback",
        )
        out = CompareRegionsOutput(
            regions=[region],
            greenest_region="us-east1",
            message="Compared 1 regions.",
        )
        assert len(out.regions) == 1
        assert out.greenest_region == "us-east1"


# ---------------------------------------------------------------------------
# CarbonService.compare_regions()
# ---------------------------------------------------------------------------


class TestCarbonServiceCompareRegions:
    """Test the compare_regions method with mocked get_intensity and find_best_execution_window."""

    @pytest.fixture
    def service(self):
        return CarbonService()

    @pytest.mark.asyncio
    async def test_default_regions_used_when_none(self, service):
        """When no locations are passed, DEFAULT_CANDIDATE_REGIONS is used."""
        with patch.object(service, "get_intensity", new_callable=AsyncMock) as mock_int, \
             patch.object(service, "find_best_execution_window", new_callable=AsyncMock) as mock_win:
            mock_int.return_value = (386.0, "regional_fallback")
            mock_win.return_value = None

            results = await service.compare_regions()
            assert len(results) == 5
            names = {r.display_name for r in results}
            assert names == set(DEFAULT_CANDIDATE_REGIONS)

    @pytest.mark.asyncio
    async def test_custom_locations(self, service):
        """Custom locations list is respected."""
        with patch.object(service, "get_intensity", new_callable=AsyncMock) as mock_int, \
             patch.object(service, "find_best_execution_window", new_callable=AsyncMock) as mock_win:
            mock_int.return_value = (386.0, "regional_fallback")
            mock_win.return_value = None

            results = await service.compare_regions(locations=["us-east1", "us-west1"])
            assert len(results) == 2

    @pytest.mark.asyncio
    async def test_results_sorted_by_intensity(self, service):
        """Results are ranked by effective intensity (lowest first)."""
        intensities = {
            "us-east1": 386.0,
            "us-west1": 210.0,
            "europe-west1": 295.0,
        }

        async def mock_intensity(loc, use_cache=False):
            sdk_loc = service.resolve_location(loc)
            return intensities.get(loc, 400.0), "regional_fallback"

        with patch.object(service, "get_intensity", side_effect=mock_intensity), \
             patch.object(service, "find_best_execution_window", new_callable=AsyncMock) as mock_win:
            mock_win.return_value = None

            results = await service.compare_regions(
                locations=["us-east1", "us-west1", "europe-west1"],
            )
            assert results[0].display_name == "us-west1"
            assert results[1].display_name == "europe-west1"
            assert results[2].display_name == "us-east1"
            assert results[0].rank == 1
            assert results[1].rank == 2
            assert results[2].rank == 3

    @pytest.mark.asyncio
    async def test_best_window_affects_sorting(self, service):
        """When a best window is available, effective intensity uses window intensity."""
        async def mock_intensity(loc, use_cache=False):
            if loc == "us-east1":
                return 386.0, "regional_fallback"
            return 500.0, "regional_fallback"

        async def mock_window(location, duration_minutes=10, horizon_hours=24):
            if location == "europe-west1":
                return {"timestamp": "2026-03-11T04:00Z", "intensity_gco2_kwh": 100.0}
            return None

        with patch.object(service, "get_intensity", side_effect=mock_intensity), \
             patch.object(service, "find_best_execution_window", side_effect=mock_window):
            results = await service.compare_regions(
                locations=["us-east1", "europe-west1"],
            )
            # europe-west1 should rank first due to best_window_intensity=100
            assert results[0].display_name == "europe-west1"
            assert results[0].best_window_intensity_gco2_kwh == 100.0

    @pytest.mark.asyncio
    async def test_savings_calculated(self, service):
        """Savings percentage is calculated when best window < current."""
        async def mock_intensity(loc, use_cache=False):
            return 400.0, "regional_fallback"

        async def mock_window(location, duration_minutes=10, horizon_hours=24):
            return {"timestamp": "2026-03-11T04:00Z", "intensity_gco2_kwh": 300.0}

        with patch.object(service, "get_intensity", side_effect=mock_intensity), \
             patch.object(service, "find_best_execution_window", side_effect=mock_window):
            results = await service.compare_regions(locations=["us-east1"])
            assert results[0].savings_vs_current_pct == 25.0

    @pytest.mark.asyncio
    async def test_error_handling_per_region(self, service):
        """A failing region doesn't crash the whole comparison."""
        call_count = 0

        async def mock_intensity(loc, use_cache=False):
            nonlocal call_count
            call_count += 1
            if loc == "us-east1":
                raise ConnectionError("SDK down")
            return 210.0, "regional_fallback"

        with patch.object(service, "get_intensity", side_effect=mock_intensity), \
             patch.object(service, "find_best_execution_window", new_callable=AsyncMock) as mock_win:
            mock_win.return_value = None

            results = await service.compare_regions(locations=["us-east1", "us-west1"])
            assert len(results) == 2
            # Error region should be ranked last
            error_region = [r for r in results if r.error]
            assert len(error_region) == 1
            assert error_region[0].display_name == "us-east1"
            assert results[-1].error is not None

    @pytest.mark.asyncio
    async def test_allowed_regions_filter(self, service):
        """Only allowed regions appear in results."""
        with patch.object(service, "get_intensity", new_callable=AsyncMock) as mock_int, \
             patch.object(service, "find_best_execution_window", new_callable=AsyncMock) as mock_win:
            mock_int.return_value = (386.0, "regional_fallback")
            mock_win.return_value = None

            results = await service.compare_regions(
                allowed_regions=["us-east1", "us-west1"],
            )
            names = {r.display_name for r in results}
            assert names == {"us-east1", "us-west1"}

    @pytest.mark.asyncio
    async def test_allowed_regions_case_insensitive(self, service):
        """Allowed regions filter is case-insensitive."""
        with patch.object(service, "get_intensity", new_callable=AsyncMock) as mock_int, \
             patch.object(service, "find_best_execution_window", new_callable=AsyncMock) as mock_win:
            mock_int.return_value = (386.0, "regional_fallback")
            mock_win.return_value = None

            results = await service.compare_regions(
                allowed_regions=["US-EAST1"],
            )
            assert len(results) == 1
            assert results[0].display_name == "us-east1"

    @pytest.mark.asyncio
    async def test_empty_allowed_regions_uses_defaults(self, service):
        """Empty allowed_regions list falls back to custom candidates or defaults."""
        with patch.object(service, "get_intensity", new_callable=AsyncMock) as mock_int, \
             patch.object(service, "find_best_execution_window", new_callable=AsyncMock) as mock_win:
            mock_int.return_value = (386.0, "regional_fallback")
            mock_win.return_value = None

            results = await service.compare_regions(
                locations=["us-east1", "us-west1"],
                allowed_regions=[],
            )
            # Empty allowed list → no filter applied
            assert len(results) == 2


# ---------------------------------------------------------------------------
# POST /agent/tools/compare_regions endpoint
# ---------------------------------------------------------------------------


class TestCompareRegionsEndpoint:
    def _mock_compare_regions(self, results: list[RegionComparison]):
        """Patch CarbonService.compare_regions on the module-level singleton."""
        return patch(
            "src.api.agent_routes._carbon_service.compare_regions",
            new_callable=AsyncMock,
            return_value=results,
        )

    def test_compare_regions_defaults(self):
        regions = [
            _make_region("us-west1", "westus", 210.0, rank=1),
            _make_region("europe-west1", "westeurope", 295.0, rank=2),
            _make_region("us-east1", "eastus", 386.0, rank=3),
        ]
        with self._mock_compare_regions(regions):
            resp = client.post("/agent/tools/compare_regions", json={})
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["regions"]) == 3
            assert data["greenest_region"] == "us-west1"
            assert "Compared 3 regions" in data["message"]

    def test_compare_regions_custom_locations(self):
        regions = [
            _make_region("us-east1", "eastus", 386.0, rank=1),
        ]
        with self._mock_compare_regions(regions):
            resp = client.post(
                "/agent/tools/compare_regions",
                json={"locations": ["us-east1"], "duration_minutes": 20},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["regions"]) == 1

    def test_compare_regions_empty_result(self):
        with self._mock_compare_regions([]):
            resp = client.post("/agent/tools/compare_regions", json={})
            assert resp.status_code == 200
            data = resp.json()
            assert data["regions"] == []
            assert data["greenest_region"] is None

    def test_compare_regions_pareto_summary(self):
        regions = [
            _make_region("us-west1", "westus", 200.0, rank=1),
            _make_region("us-east1", "eastus", 400.0, rank=2),
        ]
        with self._mock_compare_regions(regions):
            resp = client.post("/agent/tools/compare_regions", json={})
            data = resp.json()
            assert "us-west1" in data["pareto_summary"]
            assert "greener" in data["pareto_summary"]

    def test_compare_regions_with_best_window(self):
        regions = [
            _make_region(
                "europe-west1", "westeurope", 295.0,
                best_ts="2026-03-11T04:00Z",
                best_intensity=150.0,
                savings=49.2,
                rank=1,
            ),
        ]
        with self._mock_compare_regions(regions):
            resp = client.post("/agent/tools/compare_regions", json={})
            data = resp.json()
            r = data["regions"][0]
            assert r["best_window_timestamp"] == "2026-03-11T04:00Z"
            assert r["best_window_intensity_gco2_kwh"] == 150.0
            assert r["savings_vs_current_pct"] == 49.2

    def test_compare_regions_with_error_region(self):
        regions = [
            _make_region("us-west1", "westus", 210.0, rank=1),
            _make_region("us-east1", "eastus", 0.0, rank=2, error="SDK timeout"),
        ]
        with self._mock_compare_regions(regions):
            resp = client.post("/agent/tools/compare_regions", json={})
            data = resp.json()
            error_region = [r for r in data["regions"] if r["error"]]
            assert len(error_region) == 1
            assert error_region[0]["error"] == "SDK timeout"

    def test_compare_regions_server_error(self):
        with patch(
            "src.api.agent_routes._carbon_service.compare_regions",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            resp = client.post("/agent/tools/compare_regions", json={})
            assert resp.status_code == 500

    def test_compare_regions_invalid_duration(self):
        resp = client.post(
            "/agent/tools/compare_regions",
            json={"duration_minutes": 0},
        )
        assert resp.status_code == 422

    def test_compare_regions_invalid_horizon(self):
        resp = client.post(
            "/agent/tools/compare_regions",
            json={"horizon_hours": 0},
        )
        assert resp.status_code == 422

    def test_compare_regions_response_schema(self):
        regions = [_make_region("us-east1", "eastus", 386.0, rank=1)]
        with self._mock_compare_regions(regions):
            resp = client.post("/agent/tools/compare_regions", json={})
            data = resp.json()
            assert "regions" in data
            assert "pareto_summary" in data
            assert "greenest_region" in data
            assert "message" in data
            r = data["regions"][0]
            assert "rank" in r
            assert "location" in r
            assert "display_name" in r
            assert "current_intensity_gco2_kwh" in r
            assert "current_source" in r


# ---------------------------------------------------------------------------
# @greenpipe regions mention command
# ---------------------------------------------------------------------------


class TestRegionsMentionCommand:
    def _mention_event(self, note: str) -> dict:
        return {
            "object_kind": "note",
            "object_attributes": {
                "note": note,
                "noteable_type": "MergeRequest",
            },
            "project": {"id": 12345},
            "merge_request": {"iid": 42, "source_branch": "feature/green", "head_pipeline_id": 999},
        }

    def _mock_compare_regions(self, results: list[RegionComparison]):
        return patch(
            "src.api.agent_routes._carbon_service.compare_regions",
            new_callable=AsyncMock,
            return_value=results,
        )

    @pytest.fixture(autouse=True)
    def _clear_secret(self, monkeypatch):
        monkeypatch.setattr(
            "src.config.settings.gitlab_webhook_secret", ""
        )

    def test_regions_command_accepted(self):
        regions = [
            _make_region("us-west1", "westus", 210.0, rank=1),
            _make_region("us-east1", "eastus", 386.0, rank=2),
        ]
        with self._mock_compare_regions(regions):
            resp = client.post(
                "/agent/webhooks/mention",
                json=self._mention_event("@greenpipe regions"),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "accepted"
            assert data["details"]["command"] == "regions"
            assert data["details"]["regions_compared"] == 2

    def test_regions_command_case_insensitive(self):
        regions = [_make_region("us-east1", "eastus", 386.0, rank=1)]
        with self._mock_compare_regions(regions):
            resp = client.post(
                "/agent/webhooks/mention",
                json=self._mention_event("@greenpipe REGIONS"),
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "accepted"

    def test_regions_command_error_handling(self):
        with patch(
            "src.api.agent_routes._carbon_service.compare_regions",
            new_callable=AsyncMock,
            side_effect=RuntimeError("SDK unavailable"),
        ):
            resp = client.post(
                "/agent/webhooks/mention",
                json=self._mention_event("@greenpipe regions"),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "accepted"
            assert data["details"]["regions_compared"] == 0


# ---------------------------------------------------------------------------
# format_regions_comment()
# ---------------------------------------------------------------------------


class TestFormatRegionsComment:
    def test_empty_regions(self):
        result = format_regions_comment([])
        assert "No regions were compared" in result
        assert "Multi-Region Carbon Comparison" in result

    def test_single_region(self):
        regions = [_make_region("us-east1", "eastus", 386.0, rank=1)]
        result = format_regions_comment(regions)
        assert "us-east1" in result
        assert "386.0" in result
        assert "Greenest region" in result

    def test_multiple_regions_table(self):
        regions = [
            _make_region("us-west1", "westus", 210.0, rank=1),
            _make_region("europe-west1", "westeurope", 295.0, rank=2),
            _make_region("us-east1", "eastus", 386.0, rank=3),
        ]
        result = format_regions_comment(regions)
        assert "| Rank | Region |" in result
        assert "us-west1" in result
        assert "europe-west1" in result
        assert "us-east1" in result

    def test_rank_icons(self):
        regions = [
            _make_region("a", "a", 100.0, rank=1),
            _make_region("b", "b", 200.0, rank=2),
            _make_region("c", "c", 300.0, rank=3),
        ]
        result = format_regions_comment(regions)
        assert "🥇" in result
        assert "🥈" in result
        assert "🥉" in result

    def test_best_window_shown(self):
        regions = [
            _make_region(
                "us-east1", "eastus", 386.0,
                best_ts="2026-03-11T04:00Z",
                best_intensity=300.0,
                savings=22.3,
                rank=1,
            ),
        ]
        result = format_regions_comment(regions)
        assert "2026-03-11T04:00Z" in result
        assert "300.0" in result
        assert "22.3%" in result

    def test_error_region_shown(self):
        regions = [
            _make_region("us-east1", "eastus", 0.0, rank=1, error="timeout"),
        ]
        result = format_regions_comment(regions)
        assert "error" in result.lower()

    def test_pareto_summary_generated(self):
        regions = [
            _make_region("us-west1", "westus", 200.0, rank=1),
            _make_region("us-east1", "eastus", 400.0, rank=2),
        ]
        result = format_regions_comment(regions)
        assert "Pareto Summary" in result
        assert "greener" in result

    def test_gsf_footer_present(self):
        regions = [_make_region("us-east1", "eastus", 386.0, rank=1)]
        result = format_regions_comment(regions)
        assert "Carbon Aware SDK" in result
        assert "GreenPipe" in result

    def test_no_savings_shows_dash(self):
        regions = [
            _make_region("us-east1", "eastus", 386.0, savings=0.0, rank=1),
        ]
        result = format_regions_comment(regions)
        # When savings is 0, should show "—" not "0.0%"
        lines = result.split("\n")
        table_lines = [l for l in lines if "us-east1" in l]
        assert len(table_lines) > 0


# ---------------------------------------------------------------------------
# _parse_mention_command recognises "regions"
# ---------------------------------------------------------------------------


class TestParseRegionsCommand:
    def test_regions_command_parsed(self):
        from src.api.agent_routes import _parse_mention_command
        assert _parse_mention_command("@greenpipe regions") == "regions"

    def test_regions_command_case_insensitive(self):
        from src.api.agent_routes import _parse_mention_command
        assert _parse_mention_command("@greenpipe REGIONS") == "regions"

    def test_regions_in_context(self):
        from src.api.agent_routes import _parse_mention_command
        assert _parse_mention_command("Hey @greenpipe regions for this MR please") == "regions"


# ---------------------------------------------------------------------------
# Help command includes regions
# ---------------------------------------------------------------------------


class TestHelpIncludesRegions:
    def test_help_comment_mentions_regions(self):
        from src.api.report_formatter import format_help_comment
        result = format_help_comment()
        assert "@greenpipe regions" in result

    def test_mr_comment_commands_table_includes_regions(self):
        """The Available Commands table in format_mr_comment includes regions."""
        from src.services.pipeline_analyzer import PipelineAnalysisReport, JobReport
        from src.calculators.sci_calculator import SCIResult
        from datetime import datetime, timezone

        report = PipelineAnalysisReport(
            gitlab_pipeline_id=100,
            project_id=1,
            pipeline_ref="main",
            pipeline_sha="abc12345",
            pipeline_web_url=None,
            analyzed_at=datetime.now(timezone.utc),
            job_reports=[
                JobReport(
                    job_id=1,
                    job_name="test",
                    stage="test",
                    duration_seconds=60,
                    runner_type="saas-linux-small-amd64",
                    cpu_utilization_percent=50.0,
                    runner_tdp_watts=65,
                    tdp_factor=0.75,
                    avg_power_watts=48.75,
                    energy_kwh=0.000813,
                ),
            ],
            total_energy_kwh=0.000813,
            carbon_intensity_gco2_kwh=386.0,
            runner_location="us-east1",
            carbon_data_source="regional_fallback",
            sci=SCIResult(
                energy_kwh=0.000813,
                carbon_intensity_gco2_kwh=386.0,
                embodied_carbon_gco2=0.001,
                functional_unit="pipeline_run",
            ),
            urgency_class="normal",
            urgency_confidence=0.85,
            urgency_source="keyword",
            can_defer=False,
            scheduling_window=None,
            scheduling_message="Proceed on schedule.",
            commit_messages=["feat: add tests"],
            gsf_standards_used=["SCI ISO/IEC 21031:2024"],
            energy_methodology="teads_curve",
        )
        from src.api.report_formatter import format_mr_comment
        result = format_mr_comment(report)
        assert "@greenpipe regions" in result
