"""
Carbon intensity service for GreenPipe.

Fetches real-time and forecast carbon intensity data from the
GSF Carbon Aware SDK REST API.

References:
- Carbon Aware SDK: https://github.com/Green-Software-Foundation/carbon-aware-sdk
- SDK WebAPI docs: https://carbon-aware-sdk.greensoftware.foundation/
- Electricity Maps (fallback): https://electricitymaps.com/
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache
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
# In-memory cache (simple TTL cache keyed by location + hour)
# ---------------------------------------------------------------------------


class _IntensityCache:
    """Lightweight TTL cache for carbon intensity values (1-hour buckets)."""

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
        return None

    def set(self, location: str, value: float) -> None:
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
                    "falling back to regional average.",
                    rating,
                    sdk_location,
                )

        # Fallback to regional average
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

    async def close(self) -> None:
        await self._sdk.close()


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
