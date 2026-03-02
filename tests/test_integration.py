"""
End-to-end integration tests for GreenPipe.

Covers the full pipeline analysis flow without a live database or GitLab API:
- Offline pipeline analysis (jobs supplied directly)
- NLP classifier integration (keyword fallback path)
- SCI calculation correctness
- Carbon service fallback path
"""

from __future__ import annotations

import asyncio

import pytest

from src.services.pipeline_analyzer import PipelineAnalyzer
from src.services.carbon_service import CarbonService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_JOBS = [
    {
        "job_name": "build",
        "runner_type": "saas-linux-medium-amd64",
        "duration_seconds": 300,
        "cpu_utilization_percent": 60.0,
    },
    {
        "job_name": "test",
        "runner_type": "saas-linux-medium-amd64",
        "duration_seconds": 600,
        "cpu_utilization_percent": 45.0,
    },
    {
        "job_name": "lint",
        "runner_type": "saas-linux-small-amd64",
        "duration_seconds": 120,
        "cpu_utilization_percent": 30.0,
    },
]


@pytest.fixture
def analyzer() -> PipelineAnalyzer:
    return PipelineAnalyzer()  # no GitLab client, offline mode


# ---------------------------------------------------------------------------
# Offline analysis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_offline_analysis_returns_report(analyzer):
    report = await analyzer.analyze_from_data(
        jobs=SAMPLE_JOBS,
        commit_messages=["feat: add user dashboard"],
        runner_location="us-east1",
    )
    assert report is not None
    assert report.total_energy_kwh > 0
    assert report.sci.sci_score > 0
    assert report.carbon_intensity_gco2_kwh > 0
    assert report.urgency_class in {"urgent", "normal", "deferrable"}
    assert len(report.job_reports) == len(SAMPLE_JOBS)


@pytest.mark.asyncio
async def test_offline_analysis_energy_is_sum_of_jobs(analyzer):
    report = await analyzer.analyze_from_data(
        jobs=SAMPLE_JOBS,
        commit_messages=[],
        runner_location="us-east1",
    )
    per_job_total = sum(jr.energy_kwh for jr in report.job_reports)
    assert report.total_energy_kwh == pytest.approx(per_job_total, rel=1e-9)


@pytest.mark.asyncio
async def test_sci_formula_components_are_consistent(analyzer):
    report = await analyzer.analyze_from_data(
        jobs=SAMPLE_JOBS,
        commit_messages=[],
        runner_location="us-east1",
    )
    sci = report.sci
    # SCI = ((E × I) + M) / R;  R=1
    expected_operational = sci.energy_kwh * sci.carbon_intensity_gco2_kwh
    assert sci.operational_carbon_gco2 == pytest.approx(expected_operational, rel=1e-6)
    assert sci.total_carbon_gco2 == pytest.approx(
        sci.operational_carbon_gco2 + sci.embodied_carbon_gco2, rel=1e-6
    )
    assert sci.sci_score == pytest.approx(sci.total_carbon_gco2, rel=1e-6)


@pytest.mark.asyncio
async def test_gsf_standards_list_populated(analyzer):
    report = await analyzer.analyze_from_data(
        jobs=SAMPLE_JOBS,
        commit_messages=[],
        runner_location="us-east1",
    )
    assert len(report.gsf_standards_used) >= 4
    standards_text = " ".join(report.gsf_standards_used).lower()
    assert "sci" in standards_text
    assert "carbon aware" in standards_text


# ---------------------------------------------------------------------------
# Urgency classification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_urgent_pipeline_cannot_defer(analyzer):
    report = await analyzer.analyze_from_data(
        jobs=SAMPLE_JOBS,
        commit_messages=["hotfix: fix critical production crash"],
        runner_location="us-east1",
    )
    assert report.urgency_class == "urgent"
    assert report.can_defer is False


@pytest.mark.asyncio
async def test_deferrable_pipeline_can_defer(analyzer):
    report = await analyzer.analyze_from_data(
        jobs=SAMPLE_JOBS,
        commit_messages=["docs: update README with new badges"],
        runner_location="us-east1",
    )
    assert report.urgency_class == "deferrable"
    assert report.can_defer is True


@pytest.mark.asyncio
async def test_normal_pipeline_not_deferred(analyzer):
    report = await analyzer.analyze_from_data(
        jobs=SAMPLE_JOBS,
        commit_messages=["feat: implement OAuth2 login"],
        runner_location="us-east1",
    )
    assert report.urgency_class == "normal"
    assert report.can_defer is False


# ---------------------------------------------------------------------------
# Carbon service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_carbon_service_returns_positive_intensity():
    service = CarbonService()
    intensity, source = await service.get_intensity("us-east1")
    assert intensity > 0
    assert isinstance(source, str)


@pytest.mark.asyncio
async def test_carbon_service_location_resolution():
    service = CarbonService()
    # Both AWS and GCP regions should resolve without error
    for region in ["us-east-1", "europe-west1", "asia-east1", "unknown-region-xyz"]:
        location = service.resolve_location(region)
        assert isinstance(location, str)
        assert len(location) > 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_job_pipeline(analyzer):
    report = await analyzer.analyze_from_data(
        jobs=[{"job_name": "build", "runner_type": "saas-linux-small-amd64",
               "duration_seconds": 120}],
        commit_messages=["fix: typo in config file"],
        runner_location="europe-west1",
    )
    assert len(report.job_reports) == 1
    assert report.total_energy_kwh > 0


@pytest.mark.asyncio
async def test_unknown_runner_type_uses_default(analyzer):
    report = await analyzer.analyze_from_data(
        jobs=[{"job_name": "job", "runner_type": "some-custom-runner-xyz",
               "duration_seconds": 300}],
        commit_messages=["feat: some feature"],
        runner_location="us-east1",
    )
    # Should still produce a valid result using default TDP
    assert report.total_energy_kwh > 0
    assert report.job_reports[0].runner_tdp_watts == pytest.approx(80.0)


@pytest.mark.asyncio
async def test_no_commit_messages_defaults_to_normal(analyzer):
    report = await analyzer.analyze_from_data(
        jobs=SAMPLE_JOBS,
        commit_messages=[],
        runner_location="us-east1",
    )
    assert report.urgency_class == "normal"
