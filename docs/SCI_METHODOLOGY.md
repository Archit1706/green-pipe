# SCI Methodology — GreenPipe

**Standard:** Software Carbon Intensity (SCI) — ISO/IEC 21031:2024
**Reference:** https://sci.greensoftware.foundation/

---

## Formula

```
SCI = ((E × I) + M) / R
```

All carbon values are in **gCO₂e** (grams of CO₂-equivalent).

---

## Step 1 — Energy (E)

**Module:** `src/estimators/energy_estimator.py`
**Methodology:** GSF Impact Framework — Teads Curve + ECO-CI SPECpower

### 1a. Map runner to hardware TDP

GitLab runner type strings are mapped to Thermal Design Power (TDP) values
derived from SPECpower CPU benchmarks, following the ECO-CI methodology.

| Runner | TDP (W) | Representative CPU |
|---|---|---|
| saas-linux-small-amd64 | 65 | Intel Xeon E5-2650 |
| saas-linux-medium-amd64 | 95 | Intel Xeon E5-2670 |
| saas-linux-large-amd64 | 125 | Intel Xeon Gold 6140 |
| saas-linux-xlarge-amd64 | 165 | Intel Xeon Gold 6154 |
| Unknown | 80 | Conservative default |

### 1b. Apply Teads curve

The Teads curve maps CPU utilisation (%) to a TDP scaling factor via
piecewise linear interpolation:

| CPU util | TDP factor |
|---|---|
| 0% | 0.12 |
| 10% | 0.32 |
| 50% | 0.75 |
| 100% | 1.02 |

```
avg_power_watts = TDP × teads_factor(cpu_utilization_%)
energy_kWh = (avg_power_watts × duration_seconds) / 3_600_000
```

**Default CPU utilisation:** 50% when telemetry is unavailable.
This is the conservative mid-range used in ECO-CI research (2.2M pipeline study).

### 1c. Sum across jobs

```
E_total = Σ energy_kWh per job
```

### Example

```
Runner: saas-linux-medium-amd64  →  TDP = 95 W
CPU utilization: 50%              →  Teads factor = 0.75
Avg power: 95 × 0.75 = 71.25 W
Duration: 10 min = 600 s
E = (71.25 × 600) / 3_600_000 = 0.011875 kWh
```

---

## Step 2 — Carbon Intensity (I)

**Module:** `src/services/carbon_service.py`
**Source:** GSF Carbon Aware SDK
**Reference:** https://github.com/Green-Software-Foundation/carbon-aware-sdk

The Carbon Aware SDK provides real-time marginal carbon intensity (gCO₂e/kWh)
for electricity grids worldwide, sourced from WattTime and Electricity Maps.

GitLab runner regions are mapped to SDK location strings:

| GitLab / Cloud region | SDK location | Typical intensity (gCO₂e/kWh) |
|---|---|---|
| us-east1, us-east-1 | eastus | ~386 |
| us-west1, us-west-2 | westus2 | ~118 |
| europe-west1 | westeurope | ~295 |
| europe-west4 | northeurope | ~180 |
| asia-east1 | eastasia | ~500 |

**Fallback:** When the SDK is unavailable, regional averages from IEA/ElectricityMaps
2024 data are used. The source is always recorded in the API response.

---

## Step 3 — Embodied Carbon (M)

**Module:** `src/calculators/sci_calculator.py`

Embodied carbon accounts for the hardware manufacturing and lifecycle emissions
amortised over the period of software use.

### MVP approach (proxy)

```
M = E × 100  (gCO₂e per kWh_operational)
```

This conservative proxy is within the range cited in the GSF SCI Guide for
typical cloud infrastructure.

### Planned improvement

Full amortisation using manufacturer EPDs (Environmental Product Declarations):

```
M = (total_server_embodied_gCO2e / server_lifetime_hours)
    × (duration_hours)
    × (vcpus_allocated / total_server_vcpus)
```

Known server embodied values (from Dell EPDs):
- 1U server (PowerEdge R640): ~2,000,000 gCO₂e
- 2U server (PowerEdge R740): ~3,000,000 gCO₂e

---

## Step 4 — Functional Unit (R)

R = **1 pipeline run** (default)

The pipeline run is a natural, measurable, and reproducible unit for CI/CD
carbon accounting. It maps directly to developer actions and enables
comparison across projects and over time.

---

## Full Calculation Example

```
Pipeline: 2-job build + test on saas-linux-medium-amd64 (us-east1)

Job 1 (build, 5 min @ 60% CPU):
  TDP factor = interp(60, [0,10,50,100], [0.12,0.32,0.75,1.02]) = 0.855
  avg_power  = 95 × 0.855 = 81.225 W
  energy     = (81.225 × 300) / 3_600_000 = 0.006769 kWh

Job 2 (test, 10 min @ 45% CPU):
  TDP factor = interp(45, ...) = 0.712 (approx)
  avg_power  = 95 × 0.712 = 67.625 W
  energy     = (67.625 × 600) / 3_600_000 = 0.011271 kWh

E_total = 0.006769 + 0.011271 = 0.018040 kWh
I       = 386 gCO₂e/kWh  (us-east1, Carbon Aware SDK fallback)
M       = 0.018040 × 100 = 1.804 gCO₂e  (proxy)

SCI = ((0.018040 × 386) + 1.804) / 1
    = (6.963 + 1.804) / 1
    = 8.767 gCO₂e per pipeline run
```

---

## Accuracy and Validation

Target: energy estimates within ±20% of ECO-CI published benchmarks
(per the plan's Week 2 validation task).

Key sources of uncertainty:
1. CPU utilisation default (50%) — actual varies by workload type
2. TDP mapping — GitLab SaaS runner exact hardware not publicly disclosed
3. Embodied carbon proxy — simplified until EPD data is integrated

These uncertainties are inherent in any estimation-based approach and are
consistent with the methodology used by ECO-CI and the GSF Impact Framework
for cases where hardware telemetry is unavailable.
