"""
Pydantic response schemas for GreenPipe analytics endpoints.

All carbon values are in gCO₂e; energy in kWh; SCI scores in gCO₂e/pipeline_run.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# /analytics/summary
# ---------------------------------------------------------------------------


class AnalyticsSummary(BaseModel):
    """Aggregate statistics across all (or project-specific) pipeline runs."""

    # Scope
    project_id: int | None = Field(
        default=None,
        description="Project filter, or null for all projects",
    )
    total_runs: int
    # Energy
    total_energy_kwh: float
    avg_energy_kwh: float
    # Carbon
    total_carbon_gco2e: float
    avg_carbon_gco2e: float
    # SCI
    avg_sci_score: float
    min_sci_score: float
    max_sci_score: float
    # Urgency breakdown
    urgent_runs: int
    normal_runs: int
    deferrable_runs: int
    # Potential savings (estimated from deferrable pipelines)
    potential_savings_gco2e: float
    potential_savings_percent: float
    # Data freshness
    note: str = ""


# ---------------------------------------------------------------------------
# /analytics/trends
# ---------------------------------------------------------------------------


class TrendsDataPoint(BaseModel):
    """One day of aggregated pipeline metrics."""

    date: str               # ISO-8601 date string (YYYY-MM-DD)
    run_count: int
    avg_sci_score: float
    total_carbon_gco2e: float
    total_energy_kwh: float


class TrendsResponse(BaseModel):
    """SCI and carbon intensity trend over a given period."""

    project_id: int | None = None
    period_days: int
    data_points: list[TrendsDataPoint]
    note: str = ""


# ---------------------------------------------------------------------------
# /analytics/top-consumers
# ---------------------------------------------------------------------------


class TopConsumerEntry(BaseModel):
    """A single pipeline run ranked by carbon footprint."""

    pipeline_id: int                    # internal DB ID
    gitlab_pipeline_id: int | None
    project_id: int | None
    sci_score_gco2e: float
    total_carbon_gco2e: float
    total_energy_kwh: float
    urgency_class: str
    analyzed_at: str                    # ISO-8601 datetime string


class TopConsumersResponse(BaseModel):
    """Highest-carbon pipeline runs."""

    limit: int
    project_id: int | None = None
    pipelines: list[TopConsumerEntry]
    note: str = ""


# ---------------------------------------------------------------------------
# /analytics/savings
# ---------------------------------------------------------------------------


class SavingsEstimate(BaseModel):
    """
    Estimated CO₂e savings achievable if all deferrable pipelines had been
    rescheduled to an assumed 20 % lower-carbon window.

    The 20 % assumption is conservative; real savings depend on Carbon Aware
    SDK forecasts at the time of each run.  A future version will replay
    actual forecast data for historical runs.
    """

    total_runs: int
    deferrable_runs: int
    deferrable_fraction: float          # 0–1
    actual_total_carbon_gco2e: float
    estimated_optimized_carbon_gco2e: float
    potential_savings_gco2e: float
    savings_percentage: float
    assumed_reduction_pct: float = 20.0
    note: str = ""


# ---------------------------------------------------------------------------
# /analytics/leaderboard
# ---------------------------------------------------------------------------


class LeaderboardEntry(BaseModel):
    """A single contributor's carbon impact summary."""

    rank: int
    author_name: str
    pipeline_count: int
    avg_sci_score: float
    total_carbon_gco2e: float
    deferred_count: int
    deferred_percent: float  # 0–100
    co2e_saved_gco2e: float


class LeaderboardResponse(BaseModel):
    """Top green contributors ranked by average SCI score (ascending)."""

    period: str = "all-time"
    entries: list[LeaderboardEntry] = Field(default_factory=list)
    note: str = ""


# ---------------------------------------------------------------------------
# /pipeline/schedule  (scheduling recommendation endpoint)
# ---------------------------------------------------------------------------


class SchedulingRecommendationResponse(BaseModel):
    """Carbon-optimal execution window for an upcoming pipeline."""

    location: str
    current_intensity_gco2_kwh: float | None = None
    best_window: dict[str, Any] | None = None
    forecast_available: bool = False
    savings_percent: float | None = None
    recommendation: str
