"""
Tests for GSF Impact Framework energy estimator.

Verifies Teads curve values and SPECpower mapper against known data points.
"""

import pytest

from src.estimators.energy_estimator import (
    GSFEnergyEstimator,
    SPECpowerMapper,
    TeadsCurveEstimator,
    _DEFAULT_RUNNER_SPEC,
    _RUNNER_SPECS,
)


class TestTeadsCurve:
    def setup_method(self):
        self.estimator = TeadsCurveEstimator()

    def test_known_breakpoints(self):
        """Curve must return exact breakpoint values."""
        assert self.estimator.get_tdp_factor(0) == pytest.approx(0.12)
        assert self.estimator.get_tdp_factor(10) == pytest.approx(0.32)
        assert self.estimator.get_tdp_factor(50) == pytest.approx(0.75)
        assert self.estimator.get_tdp_factor(100) == pytest.approx(1.02)

    def test_interpolation_midpoint(self):
        """At 30 % utilization, factor should be between 0.32 and 0.75."""
        factor = self.estimator.get_tdp_factor(30)
        assert 0.32 < factor < 0.75

    def test_clamp_below_zero(self):
        """Values below 0 should clamp to the 0 % breakpoint."""
        assert self.estimator.get_tdp_factor(-10) == pytest.approx(0.12)

    def test_clamp_above_100(self):
        """Values above 100 should clamp to the 100 % breakpoint."""
        assert self.estimator.get_tdp_factor(150) == pytest.approx(1.02)

    def test_monotonically_increasing(self):
        """Higher utilization should always produce equal or higher factor."""
        utils = [0, 5, 10, 25, 50, 75, 100]
        factors = [self.estimator.get_tdp_factor(u) for u in utils]
        for i in range(len(factors) - 1):
            assert factors[i] <= factors[i + 1]


class TestSPECpowerMapper:
    def setup_method(self):
        self.mapper = SPECpowerMapper()

    def test_exact_match(self):
        spec = self.mapper.get_runner_spec("saas-linux-medium-amd64")
        assert spec.tdp_watts == 95.0
        assert spec.vcpus == 4

    def test_tag_match(self):
        spec = self.mapper.get_runner_spec(runner_tags=["saas-linux-large-amd64"])
        assert spec.tdp_watts == 125.0

    def test_unknown_returns_default(self):
        spec = self.mapper.get_runner_spec("some-unknown-runner-xyz")
        assert spec is _DEFAULT_RUNNER_SPEC

    def test_all_known_runners_have_positive_tdp(self):
        for key, spec in _RUNNER_SPECS.items():
            assert spec.tdp_watts > 0, f"Runner {key} has non-positive TDP"


class TestGSFEnergyEstimator:
    def setup_method(self):
        self.estimator = GSFEnergyEstimator()

    def test_zero_duration_yields_zero_energy(self):
        est = self.estimator.estimate_job_energy(
            duration_seconds=0,
            runner_type="saas-linux-medium-amd64",
        )
        assert est.energy_kwh == pytest.approx(0.0)

    def test_known_calculation(self):
        """
        Manual calculation for reference:
          runner TDP = 95 W
          Teads factor at 50 % = 0.75
          avg power = 95 × 0.75 = 71.25 W
          duration = 600 s (10 min)
          energy = (71.25 × 600) / 3_600_000 = 0.011875 kWh
        """
        est = self.estimator.estimate_job_energy(
            duration_seconds=600,
            runner_type="saas-linux-medium-amd64",
            cpu_utilization_percent=50.0,
        )
        assert est.energy_kwh == pytest.approx(0.011875, rel=1e-4)
        assert est.avg_power_watts == pytest.approx(71.25, rel=1e-4)

    def test_pipeline_total_is_sum_of_jobs(self):
        jobs = [
            {"duration_seconds": 300, "runner_type": "saas-linux-small-amd64"},
            {"duration_seconds": 600, "runner_type": "saas-linux-medium-amd64"},
        ]
        total, estimates = self.estimator.estimate_pipeline_energy(jobs)
        assert total == pytest.approx(
            sum(e.energy_kwh for e in estimates), rel=1e-9
        )

    def test_higher_utilization_means_more_energy(self):
        low = self.estimator.estimate_job_energy(
            duration_seconds=600, runner_type="saas-linux-medium-amd64",
            cpu_utilization_percent=10.0,
        )
        high = self.estimator.estimate_job_energy(
            duration_seconds=600, runner_type="saas-linux-medium-amd64",
            cpu_utilization_percent=90.0,
        )
        assert high.energy_kwh > low.energy_kwh
