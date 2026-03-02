"""
Pipeline analyzer orchestrator for GreenPipe.

Coordinates GSF-based analysis across all modules:
  1. Fetch pipeline data from GitLab API
  2. Estimate energy (GSF Impact Framework — Teads curve + SPECpower)
  3. Get carbon intensity (GSF Carbon Aware SDK)
  4. Calculate SCI (ISO/IEC 21031:2024)
  5. Classify urgency (NLP — stub in Week 2, replaced in Week 3)
  6. Determine scheduling recommendation
  7. Return a structured analysis report

This module is the single entry point used by both the FastAPI routes
and (later) the GitLab Duo Agent trigger handler.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.calculators.sci_calculator import SCICalculator, SCIResult
from src.estimators.energy_estimator import EnergyEstimate, GSFEnergyEstimator
from src.services.carbon_service import CarbonService
from src.services.gitlab_client import CommitData, GitLabClient, JobData, PipelineData

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Report data classes
# ---------------------------------------------------------------------------


@dataclass
class JobReport:
    """Energy breakdown for a single job."""

    job_id: int | None
    job_name: str | None
    stage: str | None
    duration_seconds: float
    runner_type: str | None
    runner_tdp_watts: float
    cpu_utilization_percent: float
    tdp_factor: float
    avg_power_watts: float
    energy_kwh: float


@dataclass
class SchedulingWindow:
    """A recommended low-carbon execution window."""

    timestamp: str | None
    intensity_gco2_kwh: float
    location: str
    duration_minutes: int
    savings_percent: float  # vs. current intensity


@dataclass
class PipelineAnalysisReport:
    """
    Full GSF-compliant analysis report for a pipeline run.

    This is the canonical output of the PipelineAnalyzer and is
    consumed by the API layer and the GitLab Duo Agent comment formatter.
    """

    # Identity
    gitlab_pipeline_id: int | None
    project_id: int | None
    pipeline_ref: str | None
    pipeline_sha: str | None
    pipeline_web_url: str | None
    analyzed_at: datetime

    # Energy (GSF Impact Framework)
    total_energy_kwh: float
    energy_methodology: str
    job_reports: list[JobReport]

    # Carbon (GSF Carbon Aware SDK)
    runner_location: str
    carbon_intensity_gco2_kwh: float
    carbon_data_source: str

    # SCI (ISO/IEC 21031:2024)
    sci: SCIResult

    # NLP urgency
    urgency_class: str          # 'urgent' | 'normal' | 'deferrable'
    urgency_confidence: float
    urgency_source: str         # 'nlp' | 'keyword' | 'default'

    # Scheduling
    can_defer: bool
    scheduling_window: SchedulingWindow | None
    scheduling_message: str

    # Provenance
    commit_messages: list[str]
    gsf_standards_used: list[str] = field(default_factory=list)

    def carbon_saved_if_deferred_gco2(self) -> float | None:
        """
        Estimated gCO₂e saved if pipeline runs in the recommended window
        instead of now.
        """
        if not self.scheduling_window:
            return None
        delta_intensity = (
            self.carbon_intensity_gco2_kwh - self.scheduling_window.intensity_gco2_kwh
        )
        return max(0.0, self.total_energy_kwh * delta_intensity)


# ---------------------------------------------------------------------------
# Urgency keyword classifier (stub — replaced by DistilBERT in Week 3)
# ---------------------------------------------------------------------------

_URGENT_KEYWORDS = frozenset(
    {"hotfix", "critical", "security", "emergency", "urgent", "incident", "fix!"}
)
_DEFERRABLE_KEYWORDS = frozenset(
    {"docs", "readme", "chore", "refactor", "style", "lint", "typo", "cleanup", "wip"}
)


def _keyword_classify(commit_messages: list[str]) -> tuple[str, float, str]:
    """
    Simple keyword-based urgency classifier.

    Returns (urgency_class, confidence, source).
    """
    if not commit_messages:
        return "normal", 0.5, "default"

    text = " ".join(commit_messages).lower()
    # Strip punctuation so 'hotfix:' matches 'hotfix'
    tokens = set(re.split(r"[\s\W]+", text))

    if _URGENT_KEYWORDS & tokens:
        return "urgent", 0.80, "keyword"
    if _DEFERRABLE_KEYWORDS & tokens:
        return "deferrable", 0.75, "keyword"
    return "normal", 0.65, "keyword"


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

_GSF_STANDARDS = [
    "Software Carbon Intensity (SCI) — ISO/IEC 21031:2024",
    "GSF Carbon Aware SDK",
    "GSF Impact Framework — Teads Curve",
    "ECO-CI SPECpower approach",
]


class PipelineAnalyzer:
    """
    Orchestrates GSF-based pipeline carbon analysis.

    Designed to be instantiated once and reused (services hold caches).
    Supports two usage modes:
      - Live: pass project_id + pipeline_id → fetches from GitLab
      - Offline: pass pre-built JobData list → skips GitLab API calls
    """

    def __init__(
        self,
        gitlab_client: GitLabClient | None = None,
        carbon_service: CarbonService | None = None,
    ) -> None:
        self._gitlab = gitlab_client  # None = live GitLab calls skipped
        self._carbon = carbon_service or CarbonService()
        self._energy = GSFEnergyEstimator()
        self._sci = SCICalculator()

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def analyze_from_gitlab(
        self,
        project_id: int,
        pipeline_id: int,
    ) -> PipelineAnalysisReport:
        """
        Fetch live pipeline data from GitLab and run a full GSF analysis.
        """
        if self._gitlab is None:
            raise RuntimeError(
                "GitLabClient not configured. Pass a GitLabClient to PipelineAnalyzer."
            )

        pipeline: PipelineData = self._gitlab.get_pipeline(project_id, pipeline_id)
        commits: list[CommitData] = self._gitlab.get_pipeline_commits(
            project_id, pipeline_id
        )
        commit_messages = [c.message for c in commits]

        return await self._run_analysis(pipeline, commit_messages)

    async def analyze_from_data(
        self,
        jobs: list[dict[str, Any]],
        commit_messages: list[str],
        runner_location: str | None = None,
        gitlab_pipeline_id: int | None = None,
        project_id: int | None = None,
    ) -> PipelineAnalysisReport:
        """
        Run a GSF analysis from pre-supplied job dicts (no GitLab API needed).

        Each job dict should have: duration_seconds, runner_type, runner_tags,
        cpu_utilization_percent (optional), job_name (optional).
        """
        job_objects = [
            JobData(
                id=j.get("gitlab_job_id") or 0,
                name=j.get("job_name"),
                status="success",
                duration_seconds=float(j.get("duration_seconds", 0)),
                runner_type=j.get("runner_type"),
                runner_tags=j.get("runner_tags", []),
                runner_location=runner_location,
                stage=j.get("stage"),
            )
            for j in jobs
        ]

        # Build a minimal PipelineData shell
        pipeline = PipelineData(
            id=gitlab_pipeline_id or 0,
            project_id=project_id or 0,
            status="success",
            sha="",
            ref="",
            started_at=None,
            finished_at=None,
            duration_seconds=sum(j.duration_seconds for j in job_objects),
            web_url="",
            jobs=job_objects,
        )
        # Override cpu_utilization from source dicts
        for job_obj, job_dict in zip(pipeline.jobs, jobs):
            job_obj._cpu_util = float(job_dict.get("cpu_utilization_percent", 50.0))

        return await self._run_analysis(pipeline, commit_messages, override_location=runner_location)

    # ------------------------------------------------------------------
    # Internal analysis pipeline
    # ------------------------------------------------------------------

    async def _run_analysis(
        self,
        pipeline: PipelineData,
        commit_messages: list[str],
        override_location: str | None = None,
    ) -> PipelineAnalysisReport:
        # 1. Determine runner location
        location = override_location or self._infer_location(pipeline.jobs)

        # 2. Energy estimation (GSF Impact Framework)
        total_energy_kwh, job_estimates, job_reports = self._estimate_energy(pipeline.jobs)

        # 3. Carbon intensity (GSF Carbon Aware SDK)
        carbon_intensity, carbon_source = await self._carbon.get_intensity(location)

        # 4. SCI calculation (ISO/IEC 21031:2024)
        sci_result = self._sci.calculate(
            energy_kwh=total_energy_kwh,
            carbon_intensity_gco2_kwh=carbon_intensity,
            duration_seconds=pipeline.duration_seconds,
            functional_unit="pipeline_run",
        )

        # 5. Urgency classification (keyword stub — NLP replaces in Week 3)
        urgency_class, urgency_confidence, urgency_source = _keyword_classify(
            commit_messages
        )

        # 6. Scheduling recommendation
        can_defer = urgency_class == "deferrable"
        scheduling_window: SchedulingWindow | None = None
        if can_defer:
            best = await self._carbon.find_best_execution_window(
                location=location,
                duration_minutes=max(1, int(pipeline.duration_seconds / 60)),
            )
            if best and best.get("intensity_gco2_kwh", carbon_intensity) < carbon_intensity:
                saved_pct = (
                    (carbon_intensity - best["intensity_gco2_kwh"]) / carbon_intensity * 100
                )
                scheduling_window = SchedulingWindow(
                    timestamp=best.get("timestamp"),
                    intensity_gco2_kwh=best["intensity_gco2_kwh"],
                    location=location,
                    duration_minutes=best.get("duration_minutes", 10),
                    savings_percent=round(saved_pct, 1),
                )

        scheduling_message = self._build_scheduling_message(
            urgency_class, carbon_intensity, scheduling_window
        )

        return PipelineAnalysisReport(
            gitlab_pipeline_id=pipeline.id or None,
            project_id=pipeline.project_id or None,
            pipeline_ref=pipeline.ref or None,
            pipeline_sha=pipeline.sha or None,
            pipeline_web_url=pipeline.web_url or None,
            analyzed_at=datetime.now(timezone.utc),
            total_energy_kwh=total_energy_kwh,
            energy_methodology="GSF Impact Framework Teads Curve + SPECpower",
            job_reports=job_reports,
            runner_location=location,
            carbon_intensity_gco2_kwh=carbon_intensity,
            carbon_data_source=carbon_source,
            sci=sci_result,
            urgency_class=urgency_class,
            urgency_confidence=urgency_confidence,
            urgency_source=urgency_source,
            can_defer=can_defer,
            scheduling_window=scheduling_window,
            scheduling_message=scheduling_message,
            commit_messages=commit_messages,
            gsf_standards_used=_GSF_STANDARDS,
        )

    def _estimate_energy(
        self,
        jobs: list[JobData],
    ) -> tuple[float, list[EnergyEstimate], list[JobReport]]:
        """Run energy estimation for all jobs and build JobReport list."""
        total = 0.0
        estimates: list[EnergyEstimate] = []
        reports: list[JobReport] = []

        for job in jobs:
            cpu_util = getattr(job, "_cpu_util", 50.0)
            est = self._energy.estimate_job_energy(
                duration_seconds=job.duration_seconds,
                runner_type=job.runner_type,
                runner_tags=job.runner_tags,
                cpu_utilization_percent=cpu_util,
            )
            estimates.append(est)
            total += est.energy_kwh
            reports.append(
                JobReport(
                    job_id=job.id or None,
                    job_name=job.name,
                    stage=job.stage,
                    duration_seconds=job.duration_seconds,
                    runner_type=est.runner_spec.name,
                    runner_tdp_watts=est.runner_spec.tdp_watts,
                    cpu_utilization_percent=est.cpu_utilization_percent,
                    tdp_factor=est.tdp_factor,
                    avg_power_watts=est.avg_power_watts,
                    energy_kwh=est.energy_kwh,
                )
            )

        return total, estimates, reports

    def _infer_location(self, jobs: list[JobData]) -> str:
        """Pick the first non-None runner location from the job list."""
        for job in jobs:
            if job.runner_location:
                return job.runner_location
        return "us-east1"  # GitLab SaaS default region

    @staticmethod
    def _build_scheduling_message(
        urgency_class: str,
        current_intensity: float,
        window: SchedulingWindow | None,
    ) -> str:
        if urgency_class == "urgent":
            return (
                f"Pipeline classified as URGENT — run immediately. "
                f"Current carbon intensity: {current_intensity:.1f} gCO\u2082e/kWh."
            )
        if urgency_class == "deferrable" and window:
            return (
                f"Pipeline is deferrable. Recommended window: {window.timestamp} "
                f"({window.intensity_gco2_kwh:.1f} gCO\u2082e/kWh, "
                f"{window.savings_percent:.1f}% lower carbon)."
            )
        if urgency_class == "deferrable":
            return "Pipeline is deferrable but no lower-carbon window found in forecast."
        return (
            f"Pipeline classified as normal — proceed as scheduled. "
            f"Current carbon intensity: {current_intensity:.1f} gCO\u2082e/kWh."
        )
