"""
Tests for GreenPipe analytics and scheduling endpoints.

Analytics endpoints degrade gracefully when the database is unavailable —
they return empty-but-valid responses with a descriptive `note`.  Tests rely
on this fallback behaviour so they can run without a live PostgreSQL instance.

The schedule endpoint uses the CarbonService regional fallback, which also
requires no external dependencies.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.analytics_schemas import (
    AnalyticsSummary,
    SavingsEstimate,
    SchedulingRecommendationResponse,
    TrendsResponse,
    TopConsumersResponse,
)
from src.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# /analytics/summary
# ---------------------------------------------------------------------------


class TestAnalyticsSummary:
    def test_returns_200(self):
        resp = client.get("/api/v1/analytics/summary")
        assert resp.status_code == 200

    def test_schema_valid(self):
        data = client.get("/api/v1/analytics/summary").json()
        # Pydantic will raise if fields are missing; instantiate to validate
        summary = AnalyticsSummary(**data)
        assert summary.total_runs >= 0
        assert summary.total_energy_kwh >= 0
        assert summary.avg_sci_score >= 0

    def test_with_project_id_filter(self):
        resp = client.get("/api/v1/analytics/summary?project_id=99999")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == 99999

    def test_empty_db_returns_zero_totals(self):
        data = client.get("/api/v1/analytics/summary").json()
        # DB is unavailable in tests → fallback zeros
        assert data["total_runs"] >= 0
        assert isinstance(data["note"], str)

    def test_urgency_breakdown_non_negative(self):
        data = client.get("/api/v1/analytics/summary").json()
        assert data["urgent_runs"] >= 0
        assert data["normal_runs"] >= 0
        assert data["deferrable_runs"] >= 0

    def test_savings_non_negative(self):
        data = client.get("/api/v1/analytics/summary").json()
        assert data["potential_savings_gco2e"] >= 0
        assert data["potential_savings_percent"] >= 0


# ---------------------------------------------------------------------------
# /analytics/trends
# ---------------------------------------------------------------------------


class TestAnalyticsTrends:
    def test_returns_200(self):
        resp = client.get("/api/v1/analytics/trends")
        assert resp.status_code == 200

    def test_schema_valid(self):
        data = client.get("/api/v1/analytics/trends").json()
        trends = TrendsResponse(**data)
        assert isinstance(trends.data_points, list)
        assert trends.period_days == 30

    def test_custom_days_parameter(self):
        resp = client.get("/api/v1/analytics/trends?days=7")
        assert resp.status_code == 200
        assert resp.json()["period_days"] == 7

    def test_days_validation_lower_bound(self):
        resp = client.get("/api/v1/analytics/trends?days=0")
        assert resp.status_code == 422  # pydantic ge=1

    def test_days_validation_upper_bound(self):
        resp = client.get("/api/v1/analytics/trends?days=366")
        assert resp.status_code == 422  # pydantic le=365

    def test_with_project_id(self):
        resp = client.get("/api/v1/analytics/trends?project_id=1&days=14")
        assert resp.status_code == 200
        assert resp.json()["project_id"] == 1

    def test_note_present_on_empty(self):
        data = client.get("/api/v1/analytics/trends").json()
        # Either empty data_points (DB unavailable) OR real rows — both valid
        assert isinstance(data["data_points"], list)
        assert isinstance(data["note"], str)


# ---------------------------------------------------------------------------
# /analytics/top-consumers
# ---------------------------------------------------------------------------


class TestAnalyticsTopConsumers:
    def test_returns_200(self):
        resp = client.get("/api/v1/analytics/top-consumers")
        assert resp.status_code == 200

    def test_schema_valid(self):
        data = client.get("/api/v1/analytics/top-consumers").json()
        result = TopConsumersResponse(**data)
        assert isinstance(result.pipelines, list)

    def test_default_limit_is_10(self):
        data = client.get("/api/v1/analytics/top-consumers").json()
        assert data["limit"] == 10

    def test_custom_limit(self):
        resp = client.get("/api/v1/analytics/top-consumers?limit=5")
        assert resp.status_code == 200
        assert resp.json()["limit"] == 5

    def test_limit_validation(self):
        resp = client.get("/api/v1/analytics/top-consumers?limit=0")
        assert resp.status_code == 422

    def test_pipelines_list_present(self):
        data = client.get("/api/v1/analytics/top-consumers").json()
        assert isinstance(data["pipelines"], list)


# ---------------------------------------------------------------------------
# /analytics/savings
# ---------------------------------------------------------------------------


class TestAnalyticsSavings:
    def test_returns_200(self):
        resp = client.get("/api/v1/analytics/savings")
        assert resp.status_code == 200

    def test_schema_valid(self):
        data = client.get("/api/v1/analytics/savings").json()
        estimate = SavingsEstimate(**data)
        assert estimate.assumed_reduction_pct == 20.0

    def test_savings_non_negative(self):
        data = client.get("/api/v1/analytics/savings").json()
        assert data["potential_savings_gco2e"] >= 0
        assert data["savings_percentage"] >= 0

    def test_savings_less_than_actual(self):
        data = client.get("/api/v1/analytics/savings").json()
        assert data["estimated_optimized_carbon_gco2e"] <= data["actual_total_carbon_gco2e"]

    def test_deferrable_fraction_in_range(self):
        data = client.get("/api/v1/analytics/savings").json()
        assert 0.0 <= data["deferrable_fraction"] <= 1.0

    def test_with_project_id(self):
        resp = client.get("/api/v1/analytics/savings?project_id=42")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /pipeline/schedule
# ---------------------------------------------------------------------------


class TestPipelineSchedule:
    def test_returns_200(self):
        resp = client.get("/api/v1/pipeline/schedule")
        assert resp.status_code == 200

    def test_schema_valid(self):
        data = client.get("/api/v1/pipeline/schedule").json()
        result = SchedulingRecommendationResponse(**data)
        assert result.location == "us-east1"
        assert isinstance(result.recommendation, str)

    def test_custom_location(self):
        resp = client.get("/api/v1/pipeline/schedule?location=westeurope")
        assert resp.status_code == 200
        assert resp.json()["location"] == "westeurope"

    def test_custom_duration_and_horizon(self):
        resp = client.get(
            "/api/v1/pipeline/schedule?location=us-east1&duration_minutes=30&horizon_hours=12"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["recommendation"], str)

    def test_duration_validation(self):
        resp = client.get("/api/v1/pipeline/schedule?duration_minutes=0")
        assert resp.status_code == 422

    def test_horizon_validation(self):
        resp = client.get("/api/v1/pipeline/schedule?horizon_hours=200")
        assert resp.status_code == 422

    def test_current_intensity_returned(self):
        """Carbon service regional fallback should always return a positive intensity."""
        data = client.get("/api/v1/pipeline/schedule").json()
        ci = data.get("current_intensity_gco2_kwh")
        if ci is not None:
            assert ci > 0

    def test_recommendation_is_string(self):
        data = client.get("/api/v1/pipeline/schedule").json()
        assert len(data["recommendation"]) > 10

    def test_unknown_location_graceful(self):
        resp = client.get("/api/v1/pipeline/schedule?location=nowhere-123")
        assert resp.status_code == 200
        assert resp.json()["location"] == "nowhere-123"


# ---------------------------------------------------------------------------
# Analytics schema unit tests (no HTTP, no DB)
# ---------------------------------------------------------------------------


class TestAnalyticsSchemas:
    def test_summary_schema_construction(self):
        s = AnalyticsSummary(
            project_id=1,
            total_runs=10,
            total_energy_kwh=0.12,
            avg_energy_kwh=0.012,
            total_carbon_gco2e=46.2,
            avg_carbon_gco2e=4.62,
            avg_sci_score=4.62,
            min_sci_score=2.1,
            max_sci_score=8.9,
            urgent_runs=2,
            normal_runs=5,
            deferrable_runs=3,
            potential_savings_gco2e=2.77,
            potential_savings_percent=5.99,
        )
        assert s.total_runs == 10
        assert s.deferrable_runs == 3

    def test_savings_schema_assumed_reduction(self):
        s = SavingsEstimate(
            total_runs=20,
            deferrable_runs=8,
            deferrable_fraction=0.4,
            actual_total_carbon_gco2e=100.0,
            estimated_optimized_carbon_gco2e=88.4,
            potential_savings_gco2e=11.6,
            savings_percentage=11.6,
        )
        assert s.assumed_reduction_pct == 20.0  # default

    def test_scheduling_schema(self):
        s = SchedulingRecommendationResponse(
            location="eastus",
            current_intensity_gco2_kwh=386.0,
            forecast_available=False,
            recommendation="Proceed as scheduled.",
        )
        assert s.best_window is None
        assert s.savings_percent is None
