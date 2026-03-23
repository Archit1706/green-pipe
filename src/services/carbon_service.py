"""
Carbon intensity service for GreenPipe.

Fetches real-time carbon intensity data with a three-tier fallback:
  1. GSF Carbon Aware SDK REST API (self-hosted)
  2. Electricity Maps Free API (real-time, free tier — 100 req/month)
  3. Regional average fallback (IEA / ElectricityMaps 2024 data)

References:
- Carbon Aware SDK: https://github.com/Green-Software-Foundation/carbon-aware-sdk
- SDK WebAPI docs: https://carbon-aware-sdk.greensoftware.foundation/
- Electricity Maps API: https://docs.electricitymaps.com/
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GitLab runner region → Carbon Aware SDK location mapping
# ---------------------------------------------------------------------------

# Maps GitLab-known runner regions / cloud regions to Carbon Aware SDK
# location strings accepted by the GSF Carbon Aware SDK.
RUNNER_REGION_MAP: dict[str, str] = {
    # GitLab SaaS regions (Google Cloud based)
    "us-east1": "eastus",
    "us-central1": "centralus",
    "us-west1": "westus",
    "europe-west1": "westeurope",
    "europe-west4": "northeurope",
    "asia-east1": "eastasia",
    "asia-northeast1": "japaneast",
    # AWS regions (self-managed runners)
    "us-east-1": "eastus",
    "us-east-2": "eastus2",
    "us-west-1": "westus",
    "us-west-2": "westus2",
    "eu-west-1": "westeurope",
    "eu-central-1": "germanywestcentral",
    "ap-northeast-1": "japaneast",
    "ap-southeast-1": "southeastasia",
    # Azure regions
    "eastus": "eastus",
    "westeurope": "westeurope",
    "northeurope": "northeurope",
    # Generic fallback labels
    "us": "eastus",
    "eu": "westeurope",
    "asia": "eastasia",
}

# Default fallback location when runner region is unknown
DEFAULT_LOCATION = "eastus"

# Regional average carbon intensities (gCO₂e/kWh) used when the SDK
# is unavailable.  Sources: IEA, ElectricityMaps regional averages 2024.
REGIONAL_FALLBACK_INTENSITIES: dict[str, float] = {
    "eastus": 386.0,
    "eastus2": 386.0,
    "centralus": 480.0,
    "westus": 210.0,
    "westus2": 118.0,
    "westeurope": 295.0,
    "northeurope": 180.0,
    "germanywestcentral": 340.0,
    "eastasia": 500.0,
    "japaneast": 490.0,
    "southeastasia": 510.0,
}


# ---------------------------------------------------------------------------
# SDK location → Electricity Maps zone mapping
# ---------------------------------------------------------------------------
# Electricity Maps uses its own zone codes (e.g. "US-MIDA-PJM" for eastus).
# See: https://app.electricitymaps.com/zone/
SDK_TO_EMAPS_ZONE: dict[str, str] = {
    "eastus": "US-MIDA-PJM",
    "eastus2": "US-MIDA-PJM",
    "centralus": "US-MIDW-MISO",
    "westus": "US-CAL-CISO",
    "westus2": "US-NW-PACW",
    "westeurope": "NL",
    "northeurope": "IE",
    "germanywestcentral": "DE",
    "eastasia": "HK",
    "japaneast": "JP-TK",
    "southeastasia": "SG",
    "australiaeast": "AU-NSW",
}


# Default candidate regions for multi-region comparison
DEFAULT_CANDIDATE_REGIONS: list[str] = [
    "us-east1",
    "us-west1",
    "europe-west1",
    "asia-southeast1",
    "australia-southeast1",
]

# Map the candidate defaults that don't have direct entries in RUNNER_REGION_MAP
# (so resolve_location can handle them)
RUNNER_REGION_MAP.update({
    "asia-southeast1": "southeastasia",
    "australia-southeast1": "australiaeast",
})

# Add fallback intensity for Australia
REGIONAL_FALLBACK_INTENSITIES.setdefault("australiaeast", 550.0)


# ---------------------------------------------------------------------------
# Multi-region comparison result
# ---------------------------------------------------------------------------

@dataclass
class RegionComparison:
    """Result for a single region in a multi-region carbon comparison."""

    location: str              # SDK location string (e.g. "eastus")
    display_name: str          # Human-friendly region name (e.g. "us-east1")
    current_intensity_gco2_kwh: float
    current_source: str
    best_window_timestamp: str | None = None
    best_window_intensity_gco2_kwh: float | None = None
    savings_vs_current_pct: float = 0.0
    rank: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# Carbon Aware SDK client
# ---------------------------------------------------------------------------


class CarbonAwareSDKClient:
    """
    HTTP client for the GSF Carbon Aware SDK REST API.

    The SDK exposes a WebAPI that can be self-hosted or pointed at the
    public endpoint.  This client wraps the relevant endpoints with
    async/await and simple result caching.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or settings.carbon_aware_sdk_url).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=10.0,
                headers={"Accept": "application/json"},
            )
        return self._client

    async def get_current_intensity(self, location: str) -> dict[str, Any] | None:
        """
        Query the /emissions/current endpoint.

        Returns the raw SDK response dict or None on failure.
        """
        client = await self._get_client()
        try:
            resp = await client.get(
                "/emissions/current",
                params={"location": location},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("Carbon Aware SDK current intensity request failed: %s", exc)
            return None

    async def get_forecast(
        self,
        location: str,
        horizon_hours: int = 24,
    ) -> list[dict[str, Any]]:
        """
        Query the /emissions/forecasts/current endpoint.

        Returns a list of forecast data points or an empty list on failure.
        """
        client = await self._get_client()
        try:
            resp = await client.get(
                "/emissions/forecasts/current",
                params={"location": location},
            )
            resp.raise_for_status()
            data = resp.json()
            # SDK returns list; each element has forecastData list inside
            forecasts: list[dict] = []
            for item in data:
                for point in item.get("forecastData", []):
                    forecasts.append(point)
            # Limit to the requested horizon; also drop points whose timestamp
            # could not be parsed (sentinel datetime.min means invalid ISO string).
            now = datetime.now(timezone.utc)
            cutoff = now + timedelta(hours=horizon_hours)
            _min = datetime.min.replace(tzinfo=timezone.utc)
            valid = []
            for p in forecasts:
                ts = _parse_iso(p.get("timestamp", ""))
                if ts == _min:
                    logger.debug("Dropping forecast point with unparseable timestamp: %s", p)
                    continue
                if ts <= cutoff:
                    valid.append(p)
            return valid
        except Exception as exc:
            logger.warning("Carbon Aware SDK forecast request failed: %s", exc)
            return []

    async def get_best_execution_window(
        self,
        location: str,
        duration_minutes: int = 10,
        horizon_hours: int = 12,
    ) -> dict[str, Any] | None:
        """
        Query /emissions/forecasts/batch to find the lowest-carbon
        execution window within the next `horizon_hours`.

        Returns the best window dict from the SDK or None.
        """
        client = await self._get_client()
        now = datetime.now(timezone.utc)
        window_end = now + timedelta(hours=horizon_hours)
        try:
            payload = [
                {
                    "requestedAt": now.isoformat(),
                    "location": location,
                    "dataStartAt": now.isoformat(),
                    "dataEndAt": window_end.isoformat(),
                    "windowSize": duration_minutes,
                }
            ]
            resp = await client.post("/emissions/forecasts/batch", json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data:
                return data[0].get("optimalDataPoints", [None])[0]
            return None
        except Exception as exc:
            logger.warning("Carbon Aware SDK best window request failed: %s", exc)
            return None

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# ---------------------------------------------------------------------------
# Electricity Maps Free API client
# ---------------------------------------------------------------------------


class ElectricityMapsClient:
    """
    Lightweight client for the Electricity Maps free-tier API.

    Free tier: 100 requests/month, real-time carbon intensity.
    Docs: https://docs.electricitymaps.com/
    """

    BASE_URL = "https://api.electricitymap.org/v3"

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key or settings.electricity_maps_api_key
        self._client: httpx.AsyncClient | None = None

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                timeout=10.0,
                headers={
                    "auth-token": self._api_key,
                    "Accept": "application/json",
                },
            )
        return self._client

    async def get_current_intensity(self, sdk_location: str) -> float | None:
        """
        Fetch real-time carbon intensity for an SDK location.

        Returns gCO₂eq/kWh or None on failure.
        """
        if not self.available:
            return None

        zone = SDK_TO_EMAPS_ZONE.get(sdk_location)
        if not zone:
            logger.debug("No Electricity Maps zone mapping for '%s'", sdk_location)
            return None

        client = await self._get_client()
        try:
            resp = await client.get(
                "/carbon-intensity/latest",
                params={"zone": zone},
            )
            resp.raise_for_status()
            data = resp.json()
            intensity = data.get("carbonIntensity")
            if intensity is not None and 0 < float(intensity) < 10_000:
                logger.info(
                    "Electricity Maps live intensity for %s (%s): %.1f gCO₂e/kWh",
                    sdk_location, zone, float(intensity),
                )
                return float(intensity)
            return None
        except Exception as exc:
            logger.warning("Electricity Maps request failed for zone '%s': %s", zone, exc)
            return None

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# ---------------------------------------------------------------------------
# In-memory cache (simple TTL cache keyed by location + hour)
# ---------------------------------------------------------------------------


class _IntensityCache:
    """Lightweight TTL cache for carbon intensity values (1-hour buckets)."""

    _MAX_ENTRIES = 256  # prevent unbounded memory growth

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._store: dict[str, tuple[float, datetime]] = {}
        self._ttl = timedelta(seconds=ttl_seconds)

    def _key(self, location: str) -> str:
        now = datetime.now(timezone.utc)
        return f"{location}:{now.year}-{now.month}-{now.day}-{now.hour}"

    def get(self, location: str) -> float | None:
        key = self._key(location)
        if key in self._store:
            value, stored_at = self._store[key]
            if datetime.now(timezone.utc) - stored_at < self._ttl:
                return value
            # Expired — remove stale entry
            del self._store[key]
        return None

    def set(self, location: str, value: float) -> None:
        # Evict oldest entry when cache exceeds max size
        if len(self._store) >= self._MAX_ENTRIES:
            oldest_key = min(self._store, key=lambda k: self._store[k][1])
            del self._store[oldest_key]
        self._store[self._key(location)] = (value, datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# High-level CarbonService
# ---------------------------------------------------------------------------


class CarbonService:
    """
    High-level carbon intensity service used by the pipeline analyser.

    Tries the GSF Carbon Aware SDK first; falls back to regional averages
    when the SDK is unavailable (e.g. during development / testing).
    """

    def __init__(self, sdk_url: str | None = None) -> None:
        self._sdk = CarbonAwareSDKClient(sdk_url)
        self._emaps = ElectricityMapsClient()
        self._cache = _IntensityCache(ttl_seconds=3600)

    def resolve_location(self, runner_region: str | None) -> str:
        """Map a GitLab runner region string to a Carbon Aware SDK location."""
        if not runner_region:
            return DEFAULT_LOCATION
        key = runner_region.strip().lower()
        return RUNNER_REGION_MAP.get(key, DEFAULT_LOCATION)

    async def get_intensity(
        self,
        location: str,
        use_cache: bool = True,
    ) -> tuple[float, str]:
        """
        Retrieve current carbon intensity for a location.

        Returns (intensity_gco2_kwh, source_description).
        """
        sdk_location = self.resolve_location(location)

        if use_cache:
            cached = self._cache.get(sdk_location)
            if cached is not None:
                return cached, "cache"

        # --- Tier 1: GSF Carbon Aware SDK ---
        data = await self._sdk.get_current_intensity(sdk_location)
        if data:
            # SDK response schema: {"rating": float, "location": str, ...}
            try:
                rating = float(data.get("rating", 0))
            except (TypeError, ValueError):
                rating = 0.0
            # Sanity-check: intensity must be a finite, positive value in a
            # realistic range.  Grid intensities above 1 000 gCO₂e/kWh are
            # not credible; NaN/Infinity would silently corrupt downstream SCI.
            if 0 < rating < 10_000 and rating == rating:  # last clause rejects NaN
                self._cache.set(sdk_location, rating)
                return rating, "GSF Carbon Aware SDK"
            if rating != 0:
                logger.warning(
                    "Carbon Aware SDK returned out-of-range intensity %.4f for '%s'; "
                    "trying Electricity Maps.",
                    rating,
                    sdk_location,
                )

        # --- Tier 2: Electricity Maps Free API (real-time) ---
        emaps_intensity = await self._emaps.get_current_intensity(sdk_location)
        if emaps_intensity is not None:
            self._cache.set(sdk_location, emaps_intensity)
            return emaps_intensity, "Electricity Maps API (real-time)"

        # --- Tier 3: Regional average fallback ---
        fallback = REGIONAL_FALLBACK_INTENSITIES.get(
            sdk_location,
            REGIONAL_FALLBACK_INTENSITIES[DEFAULT_LOCATION],
        )
        logger.info(
            "Using regional fallback intensity for '%s': %.1f gCO₂e/kWh",
            sdk_location,
            fallback,
        )
        return fallback, f"regional average fallback ({sdk_location})"

    async def get_forecast(
        self,
        location: str,
        horizon_hours: int = 24,
    ) -> list[dict]:
        """
        Get carbon intensity forecast for a location.

        Returns list of {timestamp, intensity} dicts sorted by time.
        """
        sdk_location = self.resolve_location(location)
        raw = await self._sdk.get_forecast(sdk_location, horizon_hours)
        result = []
        for point in raw:
            result.append(
                {
                    "timestamp": point.get("timestamp"),
                    "intensity_gco2_kwh": float(point.get("value", 0)),
                    "location": sdk_location,
                }
            )
        return sorted(result, key=lambda x: x["timestamp"] or "")

    async def find_best_execution_window(
        self,
        location: str,
        duration_minutes: int = 10,
        horizon_hours: int = 12,
    ) -> dict | None:
        """
        Find the lowest-carbon execution window for a pipeline.

        Returns a dict with 'timestamp' and 'intensity_gco2_kwh' keys,
        or None if no forecast is available.
        """
        sdk_location = self.resolve_location(location)
        window = await self._sdk.get_best_execution_window(
            sdk_location, duration_minutes, horizon_hours
        )
        if window:
            return {
                "timestamp": window.get("timestamp"),
                "intensity_gco2_kwh": float(window.get("value", 0)),
                "location": sdk_location,
                "duration_minutes": duration_minutes,
            }
        # Fallback: find minimum from forecast
        forecast = await self.get_forecast(location, horizon_hours)
        if forecast:
            best = min(forecast, key=lambda x: x["intensity_gco2_kwh"])
            best["duration_minutes"] = duration_minutes
            return best
        return None

    async def compare_regions(
        self,
        locations: list[str] | None = None,
        duration_minutes: int = 10,
        horizon_hours: int = 24,
        allowed_regions: list[str] | None = None,
    ) -> list[RegionComparison]:
        """
        Query multiple regions for carbon intensity and best execution window.

        Returns a ranked list of ``RegionComparison`` sorted by effective
        intensity (best-window intensity when available, else current intensity).

        If *allowed_regions* is provided and non-empty, only locations present
        in that list are included in the result.
        """
        candidates = locations or DEFAULT_CANDIDATE_REGIONS

        # Apply allowed-regions filter
        if allowed_regions:
            allowed_set = {r.strip().lower() for r in allowed_regions}
            candidates = [c for c in candidates if c.strip().lower() in allowed_set]
            if not candidates:
                candidates = list(allowed_set)[:10]  # cap at 10

        async def _query_region(display_name: str) -> RegionComparison:
            sdk_loc = self.resolve_location(display_name)
            try:
                intensity, source = await self.get_intensity(display_name, use_cache=True)
                # Try to find the best execution window
                window = await self.find_best_execution_window(
                    location=display_name,
                    duration_minutes=duration_minutes,
                    horizon_hours=horizon_hours,
                )
                best_ts: str | None = None
                best_intensity: float | None = None
                savings_pct = 0.0

                if window:
                    best_ts = window.get("timestamp")
                    best_intensity = float(window.get("intensity_gco2_kwh", 0))
                    if intensity > 0 and best_intensity < intensity:
                        savings_pct = (intensity - best_intensity) / intensity * 100

                return RegionComparison(
                    location=sdk_loc,
                    display_name=display_name,
                    current_intensity_gco2_kwh=round(intensity, 2),
                    current_source=source,
                    best_window_timestamp=best_ts,
                    best_window_intensity_gco2_kwh=(
                        round(best_intensity, 2) if best_intensity is not None else None
                    ),
                    savings_vs_current_pct=round(savings_pct, 1),
                )
            except Exception as exc:
                logger.warning("compare_regions: error querying '%s': %s", display_name, exc)
                return RegionComparison(
                    location=sdk_loc,
                    display_name=display_name,
                    current_intensity_gco2_kwh=0.0,
                    current_source="error",
                    error=str(exc),
                )

        # Query all regions concurrently
        results = await asyncio.gather(*[_query_region(c) for c in candidates])

        # Sort by effective intensity (best_window if available, else current)
        def _sort_key(r: RegionComparison) -> float:
            if r.error:
                return float("inf")
            if r.best_window_intensity_gco2_kwh is not None:
                return r.best_window_intensity_gco2_kwh
            return r.current_intensity_gco2_kwh

        ranked = sorted(results, key=_sort_key)

        # Assign ranks
        for i, r in enumerate(ranked, 1):
            r.rank = i

        return ranked

    async def close(self) -> None:
        await self._sdk.close()
        await self._emaps.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp string to an aware datetime."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return datetime.min.replace(tzinfo=timezone.utc)
