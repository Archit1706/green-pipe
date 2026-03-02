"""
Software Carbon Intensity (SCI) calculator for GreenPipe.

Implements the SCI specification per ISO/IEC 21031:2024.

Formula:  SCI = ((E × I) + M) / R

  E  – Energy consumed in kilowatt-hours (kWh)
  I  – Location-based marginal carbon intensity of the electricity grid
       in grams of CO₂-equivalent per kilowatt-hour (gCO₂e/kWh)
  M  – Embodied emissions of the hardware in gCO₂e, amortised over
       the duration of the software run
  R  – Functional unit: the reference unit against which the SCI score
       is normalised (default: one pipeline run)

References:
- SCI Specification: https://sci.greensoftware.foundation/
- ISO/IEC 21031:2024
- SCI Guide: https://sci-guide.greensoftware.foundation/
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SCIResult:
    """
    Full breakdown of an SCI calculation result.

    All carbon values are in gCO₂e (grams of CO₂-equivalent).
    """

    # Inputs
    energy_kwh: float
    carbon_intensity_gco2_kwh: float
    embodied_carbon_gco2: float
    functional_unit: str

    # Calculated components
    operational_carbon_gco2: float = field(init=False)
    total_carbon_gco2: float = field(init=False)
    sci_score: float = field(init=False)

    # Provenance
    methodology: str = "SCI ISO/IEC 21031:2024"
    embodied_method: str = ""

    def __post_init__(self) -> None:
        # E × I
        self.operational_carbon_gco2 = self.energy_kwh * self.carbon_intensity_gco2_kwh
        # (E × I) + M
        self.total_carbon_gco2 = self.operational_carbon_gco2 + self.embodied_carbon_gco2
        # ((E × I) + M) / R  — R is always 1 because we already express values
        # per functional unit before passing them in.
        self.sci_score = self.total_carbon_gco2

    def to_dict(self) -> dict:
        return {
            "sci_score_gco2e": round(self.sci_score, 6),
            "functional_unit": self.functional_unit,
            "energy_kwh": round(self.energy_kwh, 8),
            "carbon_intensity_gco2_kwh": round(self.carbon_intensity_gco2_kwh, 4),
            "operational_carbon_gco2e": round(self.operational_carbon_gco2, 6),
            "embodied_carbon_gco2e": round(self.embodied_carbon_gco2, 6),
            "total_carbon_gco2e": round(self.total_carbon_gco2, 6),
            "methodology": self.methodology,
            "embodied_method": self.embodied_method,
        }


class EmbodiedCarbonEstimator:
    """
    Simplified embodied (hardware manufacturing) carbon estimator.

    In a full implementation this would use the hardware lifecycle data
    from the GSF SCI Guide and manufacturer Environmental Product
    Declarations (EPDs). For the MVP we use a conservative proxy.

    Conservative proxy:
        Embodied ≈ operational_energy_kWh × EMBODIED_FACTOR
        where EMBODIED_FACTOR = 100 gCO₂e / kWh_operational

    This represents the additional lifecycle overhead of the hardware
    amortised over its active lifetime, relative to the operational
    energy consumed during the run.
    """

    # gCO₂e embodied per kWh of operational energy (conservative estimate)
    EMBODIED_FACTOR: float = 100.0

    # Known server hardware embodied carbon (gCO₂e total), from
    # manufacturer EPDs and GSF SCI Guide examples.
    # Values represent total manufacturing carbon for the physical server.
    _SERVER_TOTAL_EMBODIED: dict[str, float] = {
        # Dell PowerEdge R640: ~2,000 kg CO₂e total = 2,000,000 gCO₂e
        "server-1u": 2_000_000.0,
        # Dell PowerEdge R740: ~3,000 kg CO₂e
        "server-2u": 3_000_000.0,
    }
    # Average server lifetime (hours) for amortisation
    _SERVER_LIFETIME_HOURS: float = 35_040.0  # 4 years

    def estimate(
        self,
        energy_kwh: float,
        duration_seconds: float | None = None,
        server_type: str | None = None,
        vcpus_used: int = 1,
        total_vcpus: int = 4,
    ) -> tuple[float, str]:
        """
        Estimate embodied carbon for a single job/pipeline run.

        Returns (embodied_gco2e, description_of_method_used).

        Strategy A (preferred): amortise known server EPD over run duration.
        Strategy B (fallback):  proxy based on operational energy.
        """
        if duration_seconds and server_type and server_type in self._SERVER_TOTAL_EMBODIED:
            return self._amortise_server(
                server_type, duration_seconds, vcpus_used, total_vcpus
            )

        # Fallback: proportional proxy
        embodied = energy_kwh * self.EMBODIED_FACTOR
        return embodied, "proxy (operational_kwh × 100 gCO₂e/kWh)"

    def _amortise_server(
        self,
        server_type: str,
        duration_seconds: float,
        vcpus_used: int,
        total_vcpus: int,
    ) -> tuple[float, str]:
        """
        Amortise a server's total embodied carbon over the job duration,
        scaled by the fraction of vCPUs used.
        """
        total_embodied = self._SERVER_TOTAL_EMBODIED[server_type]
        vcpu_fraction = min(vcpus_used / max(total_vcpus, 1), 1.0)
        lifetime_seconds = self._SERVER_LIFETIME_HOURS * 3600.0
        amortised = total_embodied * vcpu_fraction * (duration_seconds / lifetime_seconds)
        method = f"amortised EPD ({server_type}) × vCPU fraction {vcpu_fraction:.2f}"
        return amortised, method


class SCICalculator:
    """
    Calculates Software Carbon Intensity per ISO/IEC 21031:2024.

    Usage::

        calc = SCICalculator()
        result = calc.calculate(
            energy_kwh=0.002,
            carbon_intensity_gco2_kwh=450.0,
            duration_seconds=600,
        )
        print(result.sci_score)  # gCO₂e per pipeline run
    """

    def __init__(self) -> None:
        self._embodied_estimator = EmbodiedCarbonEstimator()

    def calculate(
        self,
        energy_kwh: float,
        carbon_intensity_gco2_kwh: float,
        duration_seconds: float | None = None,
        embodied_carbon_gco2: float | None = None,
        server_type: str | None = None,
        vcpus_used: int = 1,
        total_vcpus: int = 4,
        functional_unit: str = "pipeline_run",
    ) -> SCIResult:
        """
        Calculate the SCI score.

        Parameters
        ----------
        energy_kwh:
            Total energy consumed by the pipeline/job (kWh).
        carbon_intensity_gco2_kwh:
            Grid carbon intensity at the runner's location (gCO₂e/kWh).
        duration_seconds:
            Job/pipeline duration, used for embodied carbon amortisation.
        embodied_carbon_gco2:
            If provided, use this value directly instead of estimating.
        server_type:
            Known server type for EPD-based embodied estimation.
        vcpus_used:
            Number of vCPUs allocated to the job.
        total_vcpus:
            Total vCPUs on the physical server (for amortisation fraction).
        functional_unit:
            The R in the SCI formula (default: one pipeline run).

        Returns
        -------
        SCIResult with full breakdown.
        """
        if embodied_carbon_gco2 is not None:
            m = embodied_carbon_gco2
            embodied_method = "provided"
        else:
            m, embodied_method = self._embodied_estimator.estimate(
                energy_kwh=energy_kwh,
                duration_seconds=duration_seconds,
                server_type=server_type,
                vcpus_used=vcpus_used,
                total_vcpus=total_vcpus,
            )

        return SCIResult(
            energy_kwh=energy_kwh,
            carbon_intensity_gco2_kwh=carbon_intensity_gco2_kwh,
            embodied_carbon_gco2=m,
            functional_unit=functional_unit,
            embodied_method=embodied_method,
        )
