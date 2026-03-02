"""
Energy estimation module for GreenPipe.

Implements GSF Impact Framework methodologies:
- Teads curve for CPU utilization → power factor mapping
- SPECpower-based TDP values for GitLab runner types (ECO-CI approach)

References:
- GSF Impact Framework: https://if.greensoftware.foundation/
- Teads curve methodology: https://if.greensoftware.foundation/pipelines/sci/
- ECO-CI SPECpower approach: https://www.green-coding.io/products/eco-ci/
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


# ---------------------------------------------------------------------------
# Teads Curve (GSF Impact Framework)
# ---------------------------------------------------------------------------


class TeadsCurveEstimator:
    """
    Maps CPU utilization percentage to a TDP scaling factor.

    The Teads curve is a piecewise-linear model derived from real-world
    server power measurements. It is the standard energy-estimation method
    used by the GSF Impact Framework.

    Curve breakpoints (utilization %, TDP factor):
        (0, 0.12), (10, 0.32), (50, 0.75), (100, 1.02)
    """

    _UTIL_POINTS: list[float] = [0.0, 10.0, 50.0, 100.0]
    _TDP_FACTORS: list[float] = [0.12, 0.32, 0.75, 1.02]

    def get_tdp_factor(self, cpu_utilization_percent: float) -> float:
        """
        Return the TDP scaling factor for a given CPU utilization (0–100 %).

        Uses numpy linear interpolation, clamped to the defined range.
        """
        utilization = float(np.clip(cpu_utilization_percent, 0.0, 100.0))
        return float(np.interp(utilization, self._UTIL_POINTS, self._TDP_FACTORS))


# ---------------------------------------------------------------------------
# Runner TDP database (SPECpower / ECO-CI approach)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunnerSpec:
    """Hardware specification for a GitLab runner type."""

    name: str
    tdp_watts: float
    cpu_model: str
    vcpus: int
    description: str = ""


# Curated runner → hardware mapping.
# Values derived from SPECpower benchmark data following ECO-CI methodology.
# We map GitLab SaaS runner tags to representative CPU TDP values.
_RUNNER_SPECS: dict[str, RunnerSpec] = {
    # GitLab SaaS Linux runners
    "saas-linux-small-amd64": RunnerSpec(
        name="saas-linux-small-amd64",
        tdp_watts=65.0,
        cpu_model="Intel Xeon E5-2650 (representative)",
        vcpus=2,
        description="GitLab SaaS small shared runner (2 vCPU)",
    ),
    "saas-linux-medium-amd64": RunnerSpec(
        name="saas-linux-medium-amd64",
        tdp_watts=95.0,
        cpu_model="Intel Xeon E5-2670 (representative)",
        vcpus=4,
        description="GitLab SaaS medium shared runner (4 vCPU)",
    ),
    "saas-linux-large-amd64": RunnerSpec(
        name="saas-linux-large-amd64",
        tdp_watts=125.0,
        cpu_model="Intel Xeon Gold 6140 (representative)",
        vcpus=8,
        description="GitLab SaaS large shared runner (8 vCPU)",
    ),
    "saas-linux-xlarge-amd64": RunnerSpec(
        name="saas-linux-xlarge-amd64",
        tdp_watts=165.0,
        cpu_model="Intel Xeon Gold 6154 (representative)",
        vcpus=16,
        description="GitLab SaaS xlarge shared runner (16 vCPU)",
    ),
    # Common cloud instance types (self-managed runners)
    "aws-t3.medium": RunnerSpec(
        name="aws-t3.medium",
        tdp_watts=40.0,
        cpu_model="Intel Xeon Platinum 8259CL",
        vcpus=2,
        description="AWS t3.medium (burstable)",
    ),
    "aws-c5.xlarge": RunnerSpec(
        name="aws-c5.xlarge",
        tdp_watts=85.0,
        cpu_model="Intel Xeon Platinum 8124M",
        vcpus=4,
        description="AWS c5.xlarge (compute optimised)",
    ),
    "gcp-n1-standard-4": RunnerSpec(
        name="gcp-n1-standard-4",
        tdp_watts=90.0,
        cpu_model="Intel Skylake Xeon",
        vcpus=4,
        description="GCP n1-standard-4",
    ),
}

# Default spec used when the runner type is unknown
_DEFAULT_RUNNER_SPEC = RunnerSpec(
    name="unknown",
    tdp_watts=80.0,
    cpu_model="Unknown (estimated average)",
    vcpus=4,
    description="Conservative default for unknown runner types",
)


class SPECpowerMapper:
    """
    Maps GitLab runner type strings and tags to hardware TDP values.

    Lookup order:
    1. Exact match on runner_type key
    2. Partial/tag match (any tag in the runner's tag list)
    3. Fall back to _DEFAULT_RUNNER_SPEC
    """

    def get_runner_spec(
        self,
        runner_type: str | None = None,
        runner_tags: list[str] | None = None,
    ) -> RunnerSpec:
        runner_type = (runner_type or "").strip().lower()
        runner_tags = [t.strip().lower() for t in (runner_tags or [])]

        # 1. Exact match
        if runner_type in _RUNNER_SPECS:
            return _RUNNER_SPECS[runner_type]

        # 2. Tag match
        for tag in runner_tags:
            if tag in _RUNNER_SPECS:
                return _RUNNER_SPECS[tag]
            # Partial substring match for convenience
            for key, spec in _RUNNER_SPECS.items():
                if key in tag or tag in key:
                    return spec

        # 3. Default
        return _DEFAULT_RUNNER_SPEC


# ---------------------------------------------------------------------------
# Main energy estimator
# ---------------------------------------------------------------------------


@dataclass
class EnergyEstimate:
    """Result of a job energy estimation."""

    energy_kwh: float
    methodology: str = "GSF Impact Framework Teads Curve + SPECpower"
    runner_spec: RunnerSpec = field(default_factory=lambda: _DEFAULT_RUNNER_SPEC)
    cpu_utilization_percent: float = 50.0
    tdp_factor: float = 0.0
    avg_power_watts: float = 0.0
    duration_seconds: float = 0.0


class GSFEnergyEstimator:
    """
    Main energy estimation service for CI/CD pipeline jobs.

    Combines:
    - SPECpower TDP mapping (ECO-CI methodology) for hardware baseline
    - GSF Impact Framework Teads curve for utilization scaling
    """

    def __init__(self) -> None:
        self._teads = TeadsCurveEstimator()
        self._specpower = SPECpowerMapper()

    def estimate_job_energy(
        self,
        duration_seconds: float,
        runner_type: str | None = None,
        runner_tags: list[str] | None = None,
        cpu_utilization_percent: float = 50.0,
    ) -> EnergyEstimate:
        """
        Estimate the energy consumed (kWh) by a single CI/CD job.

        Parameters
        ----------
        duration_seconds:
            How long the job ran.
        runner_type:
            GitLab runner type string (e.g. "saas-linux-medium-amd64").
        runner_tags:
            List of runner tags that may help identify the hardware.
        cpu_utilization_percent:
            Average CPU utilisation during the job (default 50 %, conservative).

        Returns
        -------
        EnergyEstimate with energy_kwh and all intermediate values.
        """
        spec = self._specpower.get_runner_spec(runner_type, runner_tags)
        tdp_factor = self._teads.get_tdp_factor(cpu_utilization_percent)
        avg_power_watts = spec.tdp_watts * tdp_factor
        energy_kwh = (avg_power_watts * duration_seconds) / 3_600_000.0

        return EnergyEstimate(
            energy_kwh=energy_kwh,
            runner_spec=spec,
            cpu_utilization_percent=cpu_utilization_percent,
            tdp_factor=tdp_factor,
            avg_power_watts=avg_power_watts,
            duration_seconds=duration_seconds,
        )

    def estimate_pipeline_energy(
        self,
        jobs: list[dict],
    ) -> tuple[float, list[EnergyEstimate]]:
        """
        Estimate total energy for a list of pipeline jobs.

        Each job dict should have:
            - duration_seconds (float)
            - runner_type (str, optional)
            - runner_tags (list[str], optional)
            - cpu_utilization_percent (float, optional)

        Returns
        -------
        (total_energy_kwh, per_job_estimates)
        """
        estimates: list[EnergyEstimate] = []
        total_kwh = 0.0

        for job in jobs:
            est = self.estimate_job_energy(
                duration_seconds=float(job.get("duration_seconds", 0)),
                runner_type=job.get("runner_type"),
                runner_tags=job.get("runner_tags", []),
                cpu_utilization_percent=float(job.get("cpu_utilization_percent", 50.0)),
            )
            estimates.append(est)
            total_kwh += est.energy_kwh

        return total_kwh, estimates
