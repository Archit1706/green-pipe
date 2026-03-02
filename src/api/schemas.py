"""
Pydantic request/response schemas for the GreenPipe API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class JobInput(BaseModel):
    """Data for a single CI/CD job within a pipeline."""

    gitlab_job_id: int | None = None
    job_name: str | None = Field(default=None, max_length=255)
    runner_type: str | None = Field(
        default=None,
        max_length=100,
        examples=["saas-linux-medium-amd64"],
    )
    runner_tags: list[str] = Field(default_factory=list, max_length=20)
    # ge=1: zero-duration jobs produce zero energy and break embodied carbon math
    # le=86400: cap at 24 hours — no real CI job runs longer
    duration_seconds: float = Field(ge=1, le=86400, examples=[600.0])
    cpu_utilization_percent: float = Field(default=50.0, ge=0, le=100)

    @field_validator("runner_tags")
    @classmethod
    def sanitize_runner_tags(cls, tags: list[str]) -> list[str]:
        """Strip whitespace and drop empty strings from runner tag list."""
        return [t.strip() for t in tags if t.strip()][:20]


class PipelineAnalyzeRequest(BaseModel):
    """
    Request body for POST /api/v1/pipeline/analyze.

    Either provide `gitlab_pipeline_id` + `project_id` for live GitLab
    fetching, or supply `jobs` directly for offline analysis.
    """

    gitlab_pipeline_id: int | None = None
    project_id: int | None = None
    runner_location: str | None = Field(
        default=None,
        description="GitLab runner region or cloud region string",
        examples=["us-east1", "europe-west1"],
    )
    jobs: list[JobInput] = Field(
        default_factory=list,
        description="Job data for offline analysis (bypasses GitLab API)",
    )
    commit_messages: list[str] = Field(
        default_factory=list,
        description="Commit messages for NLP urgency classification",
    )


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class EnergyBreakdown(BaseModel):
    total_energy_kwh: float
    methodology: str
    per_job: list[dict[str, Any]] = Field(default_factory=list)


class SCIBreakdown(BaseModel):
    sci_score_gco2e: float
    functional_unit: str
    energy_kwh: float
    carbon_intensity_gco2_kwh: float
    operational_carbon_gco2e: float
    embodied_carbon_gco2e: float
    total_carbon_gco2e: float
    methodology: str
    embodied_method: str


class CarbonIntensityInfo(BaseModel):
    location: str
    intensity_gco2_kwh: float
    source: str


class SchedulingRecommendation(BaseModel):
    can_defer: bool
    urgency_class: str
    urgency_confidence: float | None = None
    best_window: dict[str, Any] | None = None
    message: str


class PipelineAnalysisResponse(BaseModel):
    pipeline_id: int | None = None
    gitlab_pipeline_id: int | None = None
    project_id: int | None = None
    analyzed_at: datetime
    energy: EnergyBreakdown
    carbon_intensity: CarbonIntensityInfo
    sci: SCIBreakdown
    scheduling: SchedulingRecommendation
    gsf_standards_used: list[str]


class SCIReportResponse(BaseModel):
    pipeline_id: int
    sci: SCIBreakdown
    gsf_standards_used: list[str]


class StandardsInfoResponse(BaseModel):
    standards: list[dict[str, str]]
    version: str = "1.0.0"
