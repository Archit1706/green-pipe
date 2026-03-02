"""
GreenPipe analytics endpoints.

Provides historical carbon and SCI trend data stored in the PostgreSQL database,
plus a standalone scheduling recommendation endpoint that wraps the Carbon Aware SDK.

Endpoints:
  GET /api/v1/analytics/summary        — aggregate stats (all or per-project)
  GET /api/v1/analytics/trends         — SCI/carbon trend grouped by day
  GET /api/v1/analytics/top-consumers  — highest-carbon pipeline runs
  GET /api/v1/analytics/savings        — estimated CO₂e savings from deferral
  GET /api/v1/pipeline/schedule        — carbon-optimal execution window
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.analytics_schemas import (
    AnalyticsSummary,
    SavingsEstimate,
    SchedulingRecommendationResponse,
    TopConsumerEntry,
    TopConsumersResponse,
    TrendsDataPoint,
    TrendsResponse,
)
from src.database import get_session
from src.models.pipeline import PipelineRun
from src.services.carbon_service import CarbonService

logger = logging.getLogger(__name__)

analytics_router = APIRouter(prefix="/api/v1", tags=["Analytics"])

_carbon_service = CarbonService()

# Assumed carbon reduction when a deferrable pipeline is rescheduled to the
# best low-carbon window.  Conservative estimate used for savings projections.
_ASSUMED_REDUCTION_PCT = 20.0

_EMPTY_NOTE = (
    "No pipeline runs recorded yet. "
    "Analyse your first pipeline via POST /api/v1/pipeline/analyze."
)

_DB_ERROR_NOTE = (
    "Database unavailable — start PostgreSQL and run `alembic upgrade head` "
    "to enable persistent analytics."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _safe_scalar(session: AsyncSession, stmt) -> float:
    """Execute a scalar aggregation query; return 0.0 on any error."""
    try:
        result = await session.execute(stmt)
        value = result.scalar()
        return float(value) if value is not None else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# GET /analytics/summary
# ---------------------------------------------------------------------------


@analytics_router.get(
    "/analytics/summary",
    response_model=AnalyticsSummary,
    summary="Aggregate carbon statistics across pipeline runs",
)
async def analytics_summary(
    project_id: int | None = Query(default=None, description="Filter by GitLab project ID"),
    session: AsyncSession = Depends(get_session),
) -> AnalyticsSummary:
    """
    Return aggregated energy, carbon, and SCI statistics.

    When `project_id` is supplied only runs for that project are included.
    """
    try:
        base = select(PipelineRun)
        if project_id is not None:
            base = base.where(PipelineRun.project_id == project_id)

        # -- counts and sums --
        count_stmt = select(func.count()).select_from(base.subquery())
        total_runs = int(await _safe_scalar(session, count_stmt))

        if total_runs == 0:
            return AnalyticsSummary(
                project_id=project_id,
                total_runs=0,
                total_energy_kwh=0.0,
                avg_energy_kwh=0.0,
                total_carbon_gco2e=0.0,
                avg_carbon_gco2e=0.0,
                avg_sci_score=0.0,
                min_sci_score=0.0,
                max_sci_score=0.0,
                urgent_runs=0,
                normal_runs=0,
                deferrable_runs=0,
                potential_savings_gco2e=0.0,
                potential_savings_percent=0.0,
                note=_EMPTY_NOTE,
            )

        sub = base.subquery()

        total_energy = await _safe_scalar(
            session, select(func.sum(PipelineRun.energy_kwh)).select_from(sub)
        )
        avg_energy = await _safe_scalar(
            session, select(func.avg(PipelineRun.energy_kwh)).select_from(sub)
        )
        total_carbon = await _safe_scalar(
            session, select(func.sum(PipelineRun.total_carbon_gco2)).select_from(sub)
        )
        avg_carbon = await _safe_scalar(
            session, select(func.avg(PipelineRun.total_carbon_gco2)).select_from(sub)
        )
        avg_sci = await _safe_scalar(
            session, select(func.avg(PipelineRun.sci_score)).select_from(sub)
        )
        min_sci = await _safe_scalar(
            session, select(func.min(PipelineRun.sci_score)).select_from(sub)
        )
        max_sci = await _safe_scalar(
            session, select(func.max(PipelineRun.sci_score)).select_from(sub)
        )

        # urgency breakdown
        def _urgency_count(label: str) -> select:
            return select(func.count()).select_from(
                base.where(PipelineRun.urgency_classification == label).subquery()
            )

        urgent_runs = int(await _safe_scalar(session, _urgency_count("urgent")))
        normal_runs = int(await _safe_scalar(session, _urgency_count("normal")))
        deferrable_runs = int(await _safe_scalar(session, _urgency_count("deferrable")))

        # Estimated savings if deferrable pipelines had been shifted
        deferrable_carbon = await _safe_scalar(
            session,
            select(func.sum(PipelineRun.total_carbon_gco2)).select_from(
                base.where(PipelineRun.urgency_classification == "deferrable").subquery()
            ),
        )
        potential_savings = deferrable_carbon * (_ASSUMED_REDUCTION_PCT / 100)
        savings_pct = (potential_savings / total_carbon * 100) if total_carbon > 0 else 0.0

        return AnalyticsSummary(
            project_id=project_id,
            total_runs=total_runs,
            total_energy_kwh=round(total_energy, 8),
            avg_energy_kwh=round(avg_energy, 8),
            total_carbon_gco2e=round(total_carbon, 4),
            avg_carbon_gco2e=round(avg_carbon, 4),
            avg_sci_score=round(avg_sci, 4),
            min_sci_score=round(min_sci, 4),
            max_sci_score=round(max_sci, 4),
            urgent_runs=urgent_runs,
            normal_runs=normal_runs,
            deferrable_runs=deferrable_runs,
            potential_savings_gco2e=round(potential_savings, 4),
            potential_savings_percent=round(savings_pct, 2),
        )

    except Exception as exc:
        logger.warning("analytics/summary DB query failed: %s", exc)
        return AnalyticsSummary(
            project_id=project_id,
            total_runs=0,
            total_energy_kwh=0.0,
            avg_energy_kwh=0.0,
            total_carbon_gco2e=0.0,
            avg_carbon_gco2e=0.0,
            avg_sci_score=0.0,
            min_sci_score=0.0,
            max_sci_score=0.0,
            urgent_runs=0,
            normal_runs=0,
            deferrable_runs=0,
            potential_savings_gco2e=0.0,
            potential_savings_percent=0.0,
            note=_DB_ERROR_NOTE,
        )


# ---------------------------------------------------------------------------
# GET /analytics/trends
# ---------------------------------------------------------------------------


@analytics_router.get(
    "/analytics/trends",
    response_model=TrendsResponse,
    summary="SCI and carbon intensity trend over time (grouped by day)",
)
async def analytics_trends(
    project_id: int | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365, description="Lookback window in days"),
    session: AsyncSession = Depends(get_session),
) -> TrendsResponse:
    """
    Return per-day aggregates for the past `days` days.

    Useful for spotting carbon intensity improvement trends or regression spikes.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        # Use SQLAlchemy's func.date to group by calendar day
        date_col = func.date(PipelineRun.created_at).label("day")
        stmt = (
            select(
                date_col,
                func.count().label("run_count"),
                func.avg(PipelineRun.sci_score).label("avg_sci"),
                func.sum(PipelineRun.total_carbon_gco2).label("total_carbon"),
                func.sum(PipelineRun.energy_kwh).label("total_energy"),
            )
            .where(PipelineRun.created_at >= cutoff)
            .group_by(date_col)
            .order_by(date_col)
        )
        if project_id is not None:
            stmt = stmt.where(PipelineRun.project_id == project_id)

        result = await session.execute(stmt)
        rows = result.all()

        data_points = [
            TrendsDataPoint(
                date=str(row.day),
                run_count=int(row.run_count),
                avg_sci_score=round(float(row.avg_sci or 0), 4),
                total_carbon_gco2e=round(float(row.total_carbon or 0), 4),
                total_energy_kwh=round(float(row.total_energy or 0), 8),
            )
            for row in rows
        ]

        note = "" if data_points else _EMPTY_NOTE
        return TrendsResponse(
            project_id=project_id,
            period_days=days,
            data_points=data_points,
            note=note,
        )

    except Exception as exc:
        logger.warning("analytics/trends DB query failed: %s", exc)
        return TrendsResponse(
            project_id=project_id,
            period_days=days,
            data_points=[],
            note=_DB_ERROR_NOTE,
        )


# ---------------------------------------------------------------------------
# GET /analytics/top-consumers
# ---------------------------------------------------------------------------


@analytics_router.get(
    "/analytics/top-consumers",
    response_model=TopConsumersResponse,
    summary="Highest-carbon pipeline runs",
)
async def analytics_top_consumers(
    project_id: int | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> TopConsumersResponse:
    """
    Return the `limit` pipeline runs with the highest SCI scores.

    Identify which pipelines are your biggest emitters and prioritise
    optimisation efforts there first.
    """
    try:
        stmt = (
            select(PipelineRun)
            .order_by(PipelineRun.sci_score.desc())
            .limit(limit)
        )
        if project_id is not None:
            stmt = stmt.where(PipelineRun.project_id == project_id)

        result = await session.execute(stmt)
        runs = result.scalars().all()

        entries = [
            TopConsumerEntry(
                pipeline_id=run.id,
                gitlab_pipeline_id=run.gitlab_pipeline_id,
                project_id=run.project_id,
                sci_score_gco2e=round(float(run.sci_score or 0), 4),
                total_carbon_gco2e=round(float(run.total_carbon_gco2 or 0), 4),
                total_energy_kwh=round(float(run.energy_kwh or 0), 8),
                urgency_class=run.urgency_classification or "unknown",
                analyzed_at=run.created_at.isoformat() if run.created_at else "",
            )
            for run in runs
        ]

        note = "" if entries else _EMPTY_NOTE
        return TopConsumersResponse(
            limit=limit,
            project_id=project_id,
            pipelines=entries,
            note=note,
        )

    except Exception as exc:
        logger.warning("analytics/top-consumers DB query failed: %s", exc)
        return TopConsumersResponse(
            limit=limit,
            project_id=project_id,
            pipelines=[],
            note=_DB_ERROR_NOTE,
        )


# ---------------------------------------------------------------------------
# GET /analytics/savings
# ---------------------------------------------------------------------------


@analytics_router.get(
    "/analytics/savings",
    response_model=SavingsEstimate,
    summary="Estimated CO₂e savings from carbon-aware scheduling",
)
async def analytics_savings(
    project_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> SavingsEstimate:
    """
    Estimate how much CO₂e could have been saved if all *deferrable* pipeline
    runs had been rescheduled to a 20 % lower-carbon window.

    The 20 % reduction assumption is conservative; actual savings depend on
    forecast availability at the time of each run.
    """
    try:
        base = select(PipelineRun)
        if project_id is not None:
            base = base.where(PipelineRun.project_id == project_id)

        total_runs = int(await _safe_scalar(
            session, select(func.count()).select_from(base.subquery())
        ))
        total_carbon = await _safe_scalar(
            session, select(func.sum(PipelineRun.total_carbon_gco2)).select_from(base.subquery())
        )

        deferred_base = base.where(PipelineRun.urgency_classification == "deferrable")
        deferrable_runs = int(await _safe_scalar(
            session, select(func.count()).select_from(deferred_base.subquery())
        ))
        deferrable_carbon = await _safe_scalar(
            session,
            select(func.sum(PipelineRun.total_carbon_gco2)).select_from(deferred_base.subquery()),
        )

        potential_savings = deferrable_carbon * (_ASSUMED_REDUCTION_PCT / 100)
        optimised_total = total_carbon - potential_savings
        savings_pct = (potential_savings / total_carbon * 100) if total_carbon > 0 else 0.0
        deferred_fraction = (deferrable_runs / total_runs) if total_runs > 0 else 0.0

        note = "" if total_runs > 0 else _EMPTY_NOTE
        return SavingsEstimate(
            total_runs=total_runs,
            deferrable_runs=deferrable_runs,
            deferrable_fraction=round(deferred_fraction, 4),
            actual_total_carbon_gco2e=round(total_carbon, 4),
            estimated_optimized_carbon_gco2e=round(max(0.0, optimised_total), 4),
            potential_savings_gco2e=round(potential_savings, 4),
            savings_percentage=round(savings_pct, 2),
            assumed_reduction_pct=_ASSUMED_REDUCTION_PCT,
            note=note,
        )

    except Exception as exc:
        logger.warning("analytics/savings DB query failed: %s", exc)
        return SavingsEstimate(
            total_runs=0,
            deferrable_runs=0,
            deferrable_fraction=0.0,
            actual_total_carbon_gco2e=0.0,
            estimated_optimized_carbon_gco2e=0.0,
            potential_savings_gco2e=0.0,
            savings_percentage=0.0,
            assumed_reduction_pct=_ASSUMED_REDUCTION_PCT,
            note=_DB_ERROR_NOTE,
        )


# ---------------------------------------------------------------------------
# GET /pipeline/schedule
# ---------------------------------------------------------------------------


@analytics_router.get(
    "/pipeline/schedule",
    response_model=SchedulingRecommendationResponse,
    summary="Find the lowest-carbon execution window for a pipeline",
)
async def pipeline_schedule(
    location: str = Query(
        default="us-east1",
        description="Runner region or Carbon Aware SDK location string",
    ),
    duration_minutes: int = Query(
        default=10,
        ge=1,
        le=480,
        description="Expected pipeline duration in minutes",
    ),
    horizon_hours: int = Query(
        default=24,
        ge=1,
        le=168,
        description="How many hours ahead to search for a low-carbon window",
    ),
) -> SchedulingRecommendationResponse:
    """
    Query the GSF Carbon Aware SDK to find the lowest-carbon execution window
    for a pipeline of the given duration within the next `horizon_hours`.

    Falls back to regional average intensities when the SDK is unavailable.
    """
    current_intensity: float | None = None
    try:
        current_intensity, _ = await _carbon_service.get_intensity(location)
    except Exception as exc:
        logger.warning("schedule: could not get current intensity: %s", exc)

    best_window = None
    forecast_available = False
    try:
        best_window = await _carbon_service.find_best_execution_window(
            location=location,
            duration_minutes=duration_minutes,
            horizon_hours=horizon_hours,
        )
        if best_window:
            forecast_available = True
    except Exception as exc:
        logger.warning("schedule: forecast lookup failed: %s", exc)

    savings_pct: float | None = None
    if best_window and current_intensity is not None:
        raw_savings = (current_intensity - best_window["intensity_gco2_kwh"]) / current_intensity * 100
        savings_pct = round(max(0.0, raw_savings), 1)

    if best_window and savings_pct is not None and savings_pct > 0:
        recommendation = (
            f"Defer this pipeline to {best_window.get('timestamp', 'TBD')} for a "
            f"{savings_pct:.1f}% reduction in carbon intensity "
            f"({best_window['intensity_gco2_kwh']:.1f} vs "
            f"{(current_intensity or 0):.1f} gCO₂e/kWh)."
        )
    elif best_window:
        recommendation = (
            "No lower-carbon window found — current intensity is already optimal. "
            f"Run now at {(current_intensity or 0):.1f} gCO₂e/kWh."
        )
    else:
        ci_str = f"{current_intensity:.1f} gCO₂e/kWh" if current_intensity else "unknown"
        recommendation = (
            f"No forecast available for '{location}'. "
            f"Current intensity: {ci_str}. Proceed as scheduled."
        )

    return SchedulingRecommendationResponse(
        location=location,
        current_intensity_gco2_kwh=round(current_intensity, 2) if current_intensity else None,
        best_window=best_window,
        forecast_available=forecast_available,
        savings_percent=savings_pct,
        recommendation=recommendation,
    )
