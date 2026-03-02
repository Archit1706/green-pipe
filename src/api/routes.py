"""
GreenPipe API routes.

Endpoints:
  POST /api/v1/pipeline/analyze           – Analyze a pipeline using GSF standards
  GET  /api/v1/pipeline/{id}/report       – Fetch stored sustainability report
  GET  /api/v1/pipeline/{id}/sci          – Get SCI breakdown for stored pipeline
  GET  /api/v1/standards/info             – List implemented GSF standards
  GET  /api/v1/health                     – Health check
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.schemas import (
    CarbonIntensityInfo,
    EnergyBreakdown,
    PipelineAnalysisResponse,
    PipelineAnalyzeRequest,
    SCIBreakdown,
    SCIReportResponse,
    SchedulingRecommendation,
    StandardsInfoResponse,
)
from src.calculators.sci_calculator import SCICalculator
from src.database import get_session
from src.estimators.energy_estimator import GSFEnergyEstimator
from src.models.pipeline import GSFComplianceLog, PipelineJob, PipelineRun
from src.services.carbon_service import CarbonService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

# ---------------------------------------------------------------------------
# Shared service instances (created once, reused across requests)
# ---------------------------------------------------------------------------

_energy_estimator = GSFEnergyEstimator()
_sci_calculator = SCICalculator()
_carbon_service = CarbonService()

# ---------------------------------------------------------------------------
# GSF standards metadata
# ---------------------------------------------------------------------------

GSF_STANDARDS = [
    {
        "name": "Software Carbon Intensity (SCI)",
        "version": "ISO/IEC 21031:2024",
        "role": "Carbon scoring formula: SCI = ((E × I) + M) / R",
        "reference": "https://sci.greensoftware.foundation/",
    },
    {
        "name": "GSF Carbon Aware SDK",
        "version": "latest",
        "role": "Real-time and forecast grid carbon intensity data",
        "reference": "https://github.com/Green-Software-Foundation/carbon-aware-sdk",
    },
    {
        "name": "GSF Impact Framework – Teads Curve",
        "version": "latest",
        "role": "CPU utilization → power factor mapping for energy estimation",
        "reference": "https://if.greensoftware.foundation/",
    },
    {
        "name": "ECO-CI SPECpower approach",
        "version": "research",
        "role": "Runner hardware TDP mapping via SPECpower benchmarks",
        "reference": "https://www.green-coding.io/products/eco-ci/",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_urgency_placeholder(commit_messages: list[str]) -> tuple[str, float]:
    """
    Placeholder urgency classifier (stub until NLP module is implemented).

    Returns (urgency_class, confidence).
    Classes: 'urgent', 'normal', 'deferrable'
    """
    if not commit_messages:
        return "normal", 0.5

    text = " ".join(commit_messages).lower()
    urgent_keywords = {"hotfix", "critical", "security", "emergency", "urgent", "fix!"}
    deferrable_keywords = {"docs", "readme", "chore", "refactor", "style", "lint", "typo"}

    tokens = set(text.split())
    if urgent_keywords & tokens:
        return "urgent", 0.80
    if deferrable_keywords & tokens:
        return "deferrable", 0.75
    return "normal", 0.65


async def _persist_pipeline_run(
    session: AsyncSession,
    request: PipelineAnalyzeRequest,
    total_energy_kwh: float,
    job_estimates: list,
    carbon_intensity: float,
    carbon_source: str,
    sci_result,
    urgency_class: str,
    urgency_confidence: float,
) -> PipelineRun:
    """Create and persist a PipelineRun and its jobs to the database."""
    run = PipelineRun(
        gitlab_pipeline_id=request.gitlab_pipeline_id,
        project_id=request.project_id,
        runner_location=request.runner_location,
        duration_seconds=int(sum(j.duration_seconds for j in request.jobs)),
        energy_kwh=total_energy_kwh,
        carbon_intensity_gco2_kwh=carbon_intensity,
        carbon_data_source=carbon_source,
        operational_carbon_gco2=sci_result.operational_carbon_gco2,
        embodied_carbon_gco2=sci_result.embodied_carbon_gco2,
        total_carbon_gco2=sci_result.total_carbon_gco2,
        sci_score=sci_result.sci_score,
        sci_functional_unit=sci_result.functional_unit,
        urgency_classification=urgency_class,
        urgency_confidence=urgency_confidence,
    )
    session.add(run)
    await session.flush()  # get run.id

    # Persist per-job estimates
    for job_input, estimate in zip(request.jobs, job_estimates):
        job = PipelineJob(
            pipeline_run_id=run.id,
            gitlab_job_id=job_input.gitlab_job_id,
            job_name=job_input.job_name,
            runner_type=job_input.runner_type,
            runner_tags=",".join(job_input.runner_tags),
            duration_seconds=int(job_input.duration_seconds),
            cpu_utilization_percent=job_input.cpu_utilization_percent,
            energy_kwh=estimate.energy_kwh,
            runner_tdp_watts=estimate.runner_spec.tdp_watts,
            tdp_factor=estimate.tdp_factor,
        )
        session.add(job)

    # Log GSF compliance
    for std in GSF_STANDARDS:
        log = GSFComplianceLog(
            pipeline_run_id=run.id,
            standard_name=std["name"],
            standard_version=std["version"],
            compliance_status="compliant",
        )
        session.add(log)

    await session.commit()
    await session.refresh(run)
    return run


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


@router.post("/pipeline/analyze", response_model=PipelineAnalysisResponse)
async def analyze_pipeline(
    request: PipelineAnalyzeRequest,
    session: AsyncSession = Depends(get_session),
) -> PipelineAnalysisResponse:
    """
    Analyze a CI/CD pipeline using GSF standards and return an SCI report.

    This endpoint:
    1. Estimates energy using GSF Impact Framework (Teads curve + SPECpower)
    2. Fetches carbon intensity from GSF Carbon Aware SDK
    3. Calculates SCI per ISO/IEC 21031:2024
    4. Classifies urgency via NLP (placeholder until Week 3 NLP module)
    5. Generates carbon-aware scheduling recommendations
    6. Persists results to PostgreSQL
    """
    if not request.jobs:
        raise HTTPException(
            status_code=422,
            detail="No jobs provided. Supply at least one job in the 'jobs' field.",
        )

    # 1. Energy estimation (GSF Impact Framework)
    jobs_dicts = [
        {
            "duration_seconds": j.duration_seconds,
            "runner_type": j.runner_type,
            "runner_tags": j.runner_tags,
            "cpu_utilization_percent": j.cpu_utilization_percent,
        }
        for j in request.jobs
    ]
    total_energy_kwh, job_estimates = _energy_estimator.estimate_pipeline_energy(jobs_dicts)

    # 2. Carbon intensity (GSF Carbon Aware SDK)
    location = request.runner_location or "us-east1"
    carbon_intensity, carbon_source = await _carbon_service.get_intensity(location)

    # 3. SCI calculation (ISO/IEC 21031:2024)
    total_duration = sum(j.duration_seconds for j in request.jobs)
    sci_result = _sci_calculator.calculate(
        energy_kwh=total_energy_kwh,
        carbon_intensity_gco2_kwh=carbon_intensity,
        duration_seconds=total_duration,
        functional_unit="pipeline_run",
    )

    # 4. Urgency classification (stub — NLP module plugs in here in Week 3)
    urgency_class, urgency_confidence = _build_urgency_placeholder(request.commit_messages)

    # 5. Scheduling recommendation
    best_window = None
    if urgency_class == "deferrable":
        best_window = await _carbon_service.find_best_execution_window(
            location=location,
            duration_minutes=max(1, int(total_duration / 60)),
        )

    can_defer = urgency_class == "deferrable"
    if can_defer and best_window:
        sched_message = (
            f"Pipeline can be deferred to {best_window.get('timestamp', 'N/A')} "
            f"for lower carbon intensity "
            f"({best_window.get('intensity_gco2_kwh', 0):.1f} gCO₂e/kWh vs "
            f"current {carbon_intensity:.1f} gCO₂e/kWh)."
        )
    elif can_defer:
        sched_message = "Pipeline is deferrable but no forecast data available."
    else:
        sched_message = f"Pipeline classified as '{urgency_class}' — run immediately."

    # 6. Persist to database
    try:
        run = await _persist_pipeline_run(
            session=session,
            request=request,
            total_energy_kwh=total_energy_kwh,
            job_estimates=job_estimates,
            carbon_intensity=carbon_intensity,
            carbon_source=carbon_source,
            sci_result=sci_result,
            urgency_class=urgency_class,
            urgency_confidence=urgency_confidence,
        )
        pipeline_db_id = run.id
    except Exception as exc:
        logger.error("Database persistence failed: %s", exc)
        pipeline_db_id = None

    # 7. Build response
    per_job_data = [
        {
            "job_name": request.jobs[i].job_name,
            "duration_seconds": request.jobs[i].duration_seconds,
            "runner_type": est.runner_spec.name,
            "runner_tdp_watts": est.runner_spec.tdp_watts,
            "cpu_utilization_percent": est.cpu_utilization_percent,
            "tdp_factor": round(est.tdp_factor, 4),
            "avg_power_watts": round(est.avg_power_watts, 2),
            "energy_kwh": round(est.energy_kwh, 8),
        }
        for i, est in enumerate(job_estimates)
    ]

    return PipelineAnalysisResponse(
        pipeline_id=pipeline_db_id,
        gitlab_pipeline_id=request.gitlab_pipeline_id,
        project_id=request.project_id,
        analyzed_at=datetime.now(timezone.utc),
        energy=EnergyBreakdown(
            total_energy_kwh=round(total_energy_kwh, 8),
            methodology="GSF Impact Framework Teads Curve + SPECpower",
            per_job=per_job_data,
        ),
        carbon_intensity=CarbonIntensityInfo(
            location=location,
            intensity_gco2_kwh=round(carbon_intensity, 2),
            source=carbon_source,
        ),
        sci=SCIBreakdown(**sci_result.to_dict()),
        scheduling=SchedulingRecommendation(
            can_defer=can_defer,
            urgency_class=urgency_class,
            urgency_confidence=urgency_confidence,
            best_window=best_window,
            message=sched_message,
        ),
        gsf_standards_used=[s["name"] for s in GSF_STANDARDS],
    )


@router.get("/pipeline/{pipeline_id}/report", response_model=PipelineAnalysisResponse)
async def get_pipeline_report(
    pipeline_id: int,
    session: AsyncSession = Depends(get_session),
) -> PipelineAnalysisResponse:
    """Fetch the stored sustainability report for a pipeline run."""
    stmt = (
        select(PipelineRun)
        .where(PipelineRun.id == pipeline_id)
        .options(selectinload(PipelineRun.jobs))
    )
    result = await session.execute(stmt)
    run = result.scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail=f"Pipeline run {pipeline_id} not found.")

    per_job_data = [
        {
            "job_name": job.job_name,
            "duration_seconds": job.duration_seconds,
            "runner_type": job.runner_type,
            "runner_tdp_watts": float(job.runner_tdp_watts or 0),
            "energy_kwh": float(job.energy_kwh or 0),
        }
        for job in run.jobs
    ]

    sci = SCIBreakdown(
        sci_score_gco2e=float(run.sci_score or 0),
        functional_unit=run.sci_functional_unit or "pipeline_run",
        energy_kwh=float(run.energy_kwh or 0),
        carbon_intensity_gco2_kwh=float(run.carbon_intensity_gco2_kwh or 0),
        operational_carbon_gco2e=float(run.operational_carbon_gco2 or 0),
        embodied_carbon_gco2e=float(run.embodied_carbon_gco2 or 0),
        total_carbon_gco2e=float(run.total_carbon_gco2 or 0),
        methodology="SCI ISO/IEC 21031:2024",
        embodied_method="stored",
    )

    return PipelineAnalysisResponse(
        pipeline_id=run.id,
        gitlab_pipeline_id=run.gitlab_pipeline_id,
        project_id=run.project_id,
        analyzed_at=run.created_at,
        energy=EnergyBreakdown(
            total_energy_kwh=float(run.energy_kwh or 0),
            methodology=run.energy_methodology,
            per_job=per_job_data,
        ),
        carbon_intensity=CarbonIntensityInfo(
            location=run.runner_location or "unknown",
            intensity_gco2_kwh=float(run.carbon_intensity_gco2_kwh or 0),
            source=run.carbon_data_source or "stored",
        ),
        sci=sci,
        scheduling=SchedulingRecommendation(
            can_defer=run.urgency_classification == "deferrable",
            urgency_class=run.urgency_classification or "unknown",
            urgency_confidence=float(run.urgency_confidence or 0),
            message="Historical record — no live scheduling data.",
        ),
        gsf_standards_used=[s["name"] for s in GSF_STANDARDS],
    )


@router.get("/pipeline/{pipeline_id}/sci", response_model=SCIReportResponse)
async def get_pipeline_sci(
    pipeline_id: int,
    session: AsyncSession = Depends(get_session),
) -> SCIReportResponse:
    """Get just the SCI breakdown for a stored pipeline run."""
    stmt = select(PipelineRun).where(PipelineRun.id == pipeline_id)
    result = await session.execute(stmt)
    run = result.scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail=f"Pipeline run {pipeline_id} not found.")

    return SCIReportResponse(
        pipeline_id=run.id,
        sci=SCIBreakdown(
            sci_score_gco2e=float(run.sci_score or 0),
            functional_unit=run.sci_functional_unit or "pipeline_run",
            energy_kwh=float(run.energy_kwh or 0),
            carbon_intensity_gco2_kwh=float(run.carbon_intensity_gco2_kwh or 0),
            operational_carbon_gco2e=float(run.operational_carbon_gco2 or 0),
            embodied_carbon_gco2e=float(run.embodied_carbon_gco2 or 0),
            total_carbon_gco2e=float(run.total_carbon_gco2 or 0),
            methodology="SCI ISO/IEC 21031:2024",
            embodied_method="stored",
        ),
        gsf_standards_used=[s["name"] for s in GSF_STANDARDS],
    )


@router.get("/standards/info", response_model=StandardsInfoResponse)
async def get_standards_info() -> StandardsInfoResponse:
    """Return the list of GSF standards implemented by GreenPipe."""
    return StandardsInfoResponse(standards=GSF_STANDARDS)


@router.get("/health")
async def health_check() -> dict:
    """Simple health check endpoint."""
    return {"status": "ok", "service": "GreenPipe", "gsf_compliant": True}
