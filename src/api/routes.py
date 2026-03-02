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
from src.database import get_session
from src.models.pipeline import GSFComplianceLog, PipelineJob, PipelineRun
from src.services.carbon_service import CarbonService
from src.services.pipeline_analyzer import PipelineAnalysisReport, PipelineAnalyzer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

# ---------------------------------------------------------------------------
# Shared service instances (created once, reused across requests)
# ---------------------------------------------------------------------------

_carbon_service = CarbonService()
_analyzer = PipelineAnalyzer(carbon_service=_carbon_service)

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


async def _persist_report(
    session: AsyncSession,
    report: PipelineAnalysisReport,
) -> int:
    """Persist an analysis report to the database; return the new row ID."""
    run = PipelineRun(
        gitlab_pipeline_id=report.gitlab_pipeline_id,
        project_id=report.project_id,
        runner_location=report.runner_location,
        duration_seconds=int(sum(j.duration_seconds for j in report.job_reports)),
        energy_kwh=report.total_energy_kwh,
        energy_methodology=report.energy_methodology,
        carbon_intensity_gco2_kwh=report.carbon_intensity_gco2_kwh,
        carbon_data_source=report.carbon_data_source,
        operational_carbon_gco2=report.sci.operational_carbon_gco2,
        embodied_carbon_gco2=report.sci.embodied_carbon_gco2,
        total_carbon_gco2=report.sci.total_carbon_gco2,
        sci_score=report.sci.sci_score,
        sci_functional_unit=report.sci.functional_unit,
        urgency_classification=report.urgency_class,
        urgency_confidence=report.urgency_confidence,
    )
    session.add(run)
    await session.flush()

    for jr in report.job_reports:
        session.add(
            PipelineJob(
                pipeline_run_id=run.id,
                job_name=jr.job_name,
                runner_type=jr.runner_type,
                duration_seconds=int(jr.duration_seconds),
                cpu_utilization_percent=jr.cpu_utilization_percent,
                energy_kwh=jr.energy_kwh,
                runner_tdp_watts=jr.runner_tdp_watts,
                tdp_factor=jr.tdp_factor,
            )
        )

    for std in GSF_STANDARDS:
        session.add(
            GSFComplianceLog(
                pipeline_run_id=run.id,
                standard_name=std["name"],
                standard_version=std["version"],
                compliance_status="compliant",
            )
        )

    await session.commit()
    return run.id


def _report_to_response(
    report: PipelineAnalysisReport,
    pipeline_db_id: int | None = None,
) -> PipelineAnalysisResponse:
    """Convert an analysis report to the API response schema."""
    per_job = [
        {
            "job_name": jr.job_name,
            "stage": jr.stage,
            "duration_seconds": jr.duration_seconds,
            "runner_type": jr.runner_type,
            "runner_tdp_watts": jr.runner_tdp_watts,
            "cpu_utilization_percent": jr.cpu_utilization_percent,
            "tdp_factor": round(jr.tdp_factor, 4),
            "avg_power_watts": round(jr.avg_power_watts, 2),
            "energy_kwh": round(jr.energy_kwh, 8),
        }
        for jr in report.job_reports
    ]

    window = report.scheduling_window
    best_window_dict = (
        {
            "timestamp": window.timestamp,
            "intensity_gco2_kwh": window.intensity_gco2_kwh,
            "location": window.location,
            "savings_percent": window.savings_percent,
        }
        if window
        else None
    )

    return PipelineAnalysisResponse(
        pipeline_id=pipeline_db_id,
        gitlab_pipeline_id=report.gitlab_pipeline_id,
        project_id=report.project_id,
        analyzed_at=report.analyzed_at,
        energy=EnergyBreakdown(
            total_energy_kwh=round(report.total_energy_kwh, 8),
            methodology=report.energy_methodology,
            per_job=per_job,
        ),
        carbon_intensity=CarbonIntensityInfo(
            location=report.runner_location,
            intensity_gco2_kwh=round(report.carbon_intensity_gco2_kwh, 2),
            source=report.carbon_data_source,
        ),
        sci=SCIBreakdown(**report.sci.to_dict()),
        scheduling=SchedulingRecommendation(
            can_defer=report.can_defer,
            urgency_class=report.urgency_class,
            urgency_confidence=report.urgency_confidence,
            best_window=best_window_dict,
            message=report.scheduling_message,
        ),
        gsf_standards_used=[s["name"] for s in GSF_STANDARDS],
    )


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

    Provide either:
    - `gitlab_pipeline_id` + `project_id` to fetch live data (requires GITLAB_TOKEN)
    - `jobs` list for offline/demo analysis (no GitLab token needed)
    """
    if not request.jobs and not (request.gitlab_pipeline_id and request.project_id):
        raise HTTPException(
            status_code=422,
            detail="Provide either 'jobs' for offline analysis, or "
                   "'gitlab_pipeline_id' + 'project_id' for live GitLab fetching.",
        )

    try:
        if request.gitlab_pipeline_id and request.project_id and not request.jobs:
            report = await _analyzer.analyze_from_gitlab(
                project_id=request.project_id,
                pipeline_id=request.gitlab_pipeline_id,
            )
        else:
            jobs_dicts = [
                {
                    "gitlab_job_id": j.gitlab_job_id,
                    "job_name": j.job_name,
                    "runner_type": j.runner_type,
                    "runner_tags": j.runner_tags,
                    "duration_seconds": j.duration_seconds,
                    "cpu_utilization_percent": j.cpu_utilization_percent,
                }
                for j in request.jobs
            ]
            report = await _analyzer.analyze_from_data(
                jobs=jobs_dicts,
                commit_messages=request.commit_messages,
                runner_location=request.runner_location,
                gitlab_pipeline_id=request.gitlab_pipeline_id,
                project_id=request.project_id,
            )
    except Exception as exc:
        logger.error("Pipeline analysis failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc

    pipeline_db_id: int | None = None
    try:
        pipeline_db_id = await _persist_report(session, report)
    except Exception as exc:
        logger.error("DB persistence failed (returning result anyway): %s", exc)

    return _report_to_response(report, pipeline_db_id)


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

    per_job = [
        {
            "job_name": job.job_name,
            "duration_seconds": job.duration_seconds,
            "runner_type": job.runner_type,
            "runner_tdp_watts": float(job.runner_tdp_watts or 0),
            "energy_kwh": float(job.energy_kwh or 0),
        }
        for job in run.jobs
    ]

    return PipelineAnalysisResponse(
        pipeline_id=run.id,
        gitlab_pipeline_id=run.gitlab_pipeline_id,
        project_id=run.project_id,
        analyzed_at=run.created_at,
        energy=EnergyBreakdown(
            total_energy_kwh=float(run.energy_kwh or 0),
            methodology=run.energy_methodology,
            per_job=per_job,
        ),
        carbon_intensity=CarbonIntensityInfo(
            location=run.runner_location or "unknown",
            intensity_gco2_kwh=float(run.carbon_intensity_gco2_kwh or 0),
            source=run.carbon_data_source or "stored",
        ),
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
        scheduling=SchedulingRecommendation(
            can_defer=run.urgency_classification == "deferrable",
            urgency_class=run.urgency_classification or "unknown",
            urgency_confidence=float(run.urgency_confidence or 0),
            message="Historical record.",
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
