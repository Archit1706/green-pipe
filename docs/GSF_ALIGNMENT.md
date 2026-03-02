# GreenPipe – GSF Standards Alignment

This document records which Green Software Foundation (GSF) standards
GreenPipe implements, how each is used, and where to find the authoritative
reference.

---

## 1. Software Carbon Intensity (SCI) – ISO/IEC 21031:2024

**Status:** Implemented
**Reference:** https://sci.greensoftware.foundation/
**Spec version:** ISO/IEC 21031:2024

### Formula

```
SCI = ((E × I) + M) / R
```

| Variable | Meaning | GreenPipe implementation |
|---|---|---|
| E | Energy consumed (kWh) | Estimated via Teads curve + SPECpower (see §3) |
| I | Carbon intensity of electricity grid (gCO₂e/kWh) | Fetched from Carbon Aware SDK (see §2) |
| M | Embodied hardware emissions (gCO₂e), amortised | Proxy: `E × 100` or EPD amortisation |
| R | Functional unit | 1 pipeline run (default) |

### Functional unit justification

The CI/CD pipeline run is a natural, reproducible unit of software execution.
It maps directly to developer actions and is consistent across projects.

### Embodied carbon (M) – MVP approach

The MVP uses a conservative proxy: `M = E × 100 gCO₂e/kWh_operational`.
This is within the range cited in the GSF SCI Guide for typical cloud
infrastructure. Future work will use manufacturer Environmental Product
Declarations (EPDs) and the GSF SCI Guide hardware lifecycle tables.

---

## 2. GSF Carbon Aware SDK

**Status:** Integrated
**Reference:** https://github.com/Green-Software-Foundation/carbon-aware-sdk

### Usage

- `GET /emissions/current?location=<sdk_location>` — real-time grid intensity
- `GET /emissions/forecasts/current?location=<sdk_location>` — 24h forecast for scheduling
- `POST /emissions/forecasts/batch` — find the lowest-carbon execution window

### Location mapping

GitLab runner regions and AWS/GCP cloud regions are mapped to Carbon Aware SDK
location strings via `RUNNER_REGION_MAP` in `src/services/carbon_service.py`.

### Fallback

When the Carbon Aware SDK is unavailable (e.g. local development), GreenPipe
falls back to `REGIONAL_FALLBACK_INTENSITIES` — IEA/ElectricityMaps regional
averages (2024 data). The data source is always recorded in the API response.

---

## 3. GSF Impact Framework – Teads Curve

**Status:** Implemented
**Reference:** https://if.greensoftware.foundation/

### Teads curve

The Teads curve maps CPU utilisation percentage to a TDP scaling factor
using piecewise linear interpolation.

| CPU utilisation | TDP factor |
|---|---|
| 0% | 0.12 |
| 10% | 0.32 |
| 50% | 0.75 |
| 100% | 1.02 |

```python
avg_power_watts = runner_tdp_watts × teads_factor(cpu_utilization_pct)
energy_kwh = (avg_power_watts × duration_seconds) / 3_600_000
```

Implementation: `src/estimators/energy_estimator.py` – `TeadsCurveEstimator`

### Default CPU utilisation

When real CPU telemetry is unavailable (typical for GitLab SaaS runners),
GreenPipe defaults to 50% utilisation (conservative mid-range estimate,
consistent with ECO-CI research paper findings).

---

## 4. ECO-CI SPECpower Approach

**Status:** Implemented (static mapping)
**Reference:** https://www.green-coding.io/products/eco-ci/

### Runner TDP mapping

GitLab runner type strings are mapped to representative TDP values derived
from SPECpower benchmark data, following the ECO-CI methodology.

| Runner type | TDP (W) | Representative CPU |
|---|---|---|
| saas-linux-small-amd64 | 65 | Intel Xeon E5-2650 |
| saas-linux-medium-amd64 | 95 | Intel Xeon E5-2670 |
| saas-linux-large-amd64 | 125 | Intel Xeon Gold 6140 |
| saas-linux-xlarge-amd64 | 165 | Intel Xeon Gold 6154 |
| aws-t3.medium | 40 | Intel Xeon Platinum 8259CL |
| aws-c5.xlarge | 85 | Intel Xeon Platinum 8124M |
| gcp-n1-standard-4 | 90 | Intel Skylake Xeon |

Implementation: `src/estimators/energy_estimator.py` – `SPECpowerMapper`

---

## Compliance Tracking

Every pipeline analysis creates a `gsf_compliance_log` database entry for each
standard, recording:
- `standard_name` — human-readable standard name
- `standard_version` — specification version
- `compliance_status` — `compliant | partial | skipped`
- `notes` — any deviations or approximations

---

## Planned Improvements

- [ ] Full SPECpower database integration (runner → exact CPU model lookup)
- [ ] Hardware EPD data for embodied carbon estimation
- [ ] Energy measurement via cgroups on self-managed runners (actual vs. estimated)
- [ ] Contribution to GSF Impact Framework as a GitLab runner plugin
