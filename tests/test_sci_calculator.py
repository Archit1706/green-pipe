"""
Tests for the SCI calculator against ISO/IEC 21031:2024.

Uses the reference example from the SCI specification:
  E = 0.001 kWh, I = 500 gCO₂/kWh → operational = 0.5 gCO₂e
"""

import pytest

from src.calculators.sci_calculator import EmbodiedCarbonEstimator, SCICalculator, SCIResult


class TestSCIResult:
    def test_operational_carbon_formula(self):
        """Operational carbon = E × I."""
        result = SCIResult(
            energy_kwh=0.001,
            carbon_intensity_gco2_kwh=500.0,
            embodied_carbon_gco2=0.0,
            functional_unit="pipeline_run",
        )
        assert result.operational_carbon_gco2 == pytest.approx(0.5)

    def test_total_carbon_includes_embodied(self):
        """Total carbon = (E × I) + M."""
        result = SCIResult(
            energy_kwh=0.001,
            carbon_intensity_gco2_kwh=500.0,
            embodied_carbon_gco2=50.0,
            functional_unit="pipeline_run",
        )
        assert result.total_carbon_gco2 == pytest.approx(50.5)
        assert result.sci_score == pytest.approx(50.5)

    def test_to_dict_keys(self):
        result = SCIResult(
            energy_kwh=0.001,
            carbon_intensity_gco2_kwh=400.0,
            embodied_carbon_gco2=10.0,
            functional_unit="pipeline_run",
        )
        d = result.to_dict()
        required_keys = {
            "sci_score_gco2e",
            "functional_unit",
            "energy_kwh",
            "carbon_intensity_gco2_kwh",
            "operational_carbon_gco2e",
            "embodied_carbon_gco2e",
            "total_carbon_gco2e",
            "methodology",
        }
        assert required_keys.issubset(d.keys())


class TestEmbodiedCarbonEstimator:
    def setup_method(self):
        self.estimator = EmbodiedCarbonEstimator()

    def test_proxy_fallback(self):
        """Without server_type, should use proxy (energy × 100)."""
        m, method = self.estimator.estimate(energy_kwh=0.01)
        assert m == pytest.approx(1.0)  # 0.01 × 100
        assert "proxy" in method

    def test_amortised_is_positive(self):
        m, method = self.estimator.estimate(
            energy_kwh=0.01,
            duration_seconds=600,
            server_type="server-1u",
            vcpus_used=2,
            total_vcpus=16,
        )
        assert m > 0
        assert "amortised" in method

    def test_amortised_scales_with_duration(self):
        short, _ = self.estimator.estimate(
            energy_kwh=0.001, duration_seconds=60,
            server_type="server-1u", vcpus_used=1, total_vcpus=4,
        )
        long, _ = self.estimator.estimate(
            energy_kwh=0.001, duration_seconds=6000,
            server_type="server-1u", vcpus_used=1, total_vcpus=4,
        )
        assert long > short


class TestSCICalculator:
    def setup_method(self):
        self.calc = SCICalculator()

    def test_explicit_embodied_carbon_used_directly(self):
        """When embodied_carbon_gco2 is provided, skip estimation."""
        result = self.calc.calculate(
            energy_kwh=0.001,
            carbon_intensity_gco2_kwh=400.0,
            embodied_carbon_gco2=10.0,
        )
        assert result.embodied_carbon_gco2 == pytest.approx(10.0)
        assert result.embodied_method == "provided"

    def test_sci_score_is_positive_for_positive_inputs(self):
        result = self.calc.calculate(
            energy_kwh=0.002,
            carbon_intensity_gco2_kwh=450.0,
        )
        assert result.sci_score > 0

    def test_higher_carbon_intensity_gives_higher_sci(self):
        low = self.calc.calculate(
            energy_kwh=0.002,
            carbon_intensity_gco2_kwh=100.0,
            embodied_carbon_gco2=0.0,
        )
        high = self.calc.calculate(
            energy_kwh=0.002,
            carbon_intensity_gco2_kwh=800.0,
            embodied_carbon_gco2=0.0,
        )
        assert high.sci_score > low.sci_score

    def test_zero_energy_zero_operational_carbon(self):
        result = self.calc.calculate(
            energy_kwh=0.0,
            carbon_intensity_gco2_kwh=500.0,
            embodied_carbon_gco2=0.0,
        )
        assert result.operational_carbon_gco2 == pytest.approx(0.0)

    def test_functional_unit_stored_in_result(self):
        result = self.calc.calculate(
            energy_kwh=0.001,
            carbon_intensity_gco2_kwh=300.0,
            functional_unit="build",
        )
        assert result.functional_unit == "build"
