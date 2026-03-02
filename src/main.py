"""
GreenPipe FastAPI application entry point.

Built on Green Software Foundation Standards:
- Software Carbon Intensity (SCI) – ISO/IEC 21031:2024
- GSF Carbon Aware SDK
- GSF Impact Framework (Teads curve)
- ECO-CI SPECpower approach

Security notes (pre-production checklist):
- TODO: Replace CORS wildcard (allow_origins=["*"]) with the specific origin(s)
  of your GitLab instance / frontend before any public deployment.
- TODO: Add authentication middleware (e.g. HTTP Bearer token checked against
  a shared secret in GITLAB_WEBHOOK_SECRET, or OAuth2 scopes from GitLab).
  All /api/v1/ endpoints are currently unauthenticated.
- TODO: Add rate-limiting middleware (e.g. slowapi) to prevent abuse of the
  /pipeline/analyze endpoint which performs NLP inference per request.
- TODO: Enable HTTPS termination at the reverse-proxy / platform layer
  (Railway, Render, Nginx) so that the GitLab token and pipeline data are
  always transmitted over TLS.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router
from src.config import settings
from src.database import create_tables

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="GreenPipe",
    description=(
        "GSF-Compliant Carbon-Aware CI/CD Agent for GitLab. "
        "Implements SCI (ISO/IEC 21031:2024), Carbon Aware SDK, and Impact Framework."
    ),
    version="0.1.0",
    contact={
        "name": "GreenPipe",
        "url": "https://github.com/greenpipe",
    },
    license_info={"name": "MIT"},
)

# TODO (production): restrict allow_origins to your GitLab instance hostname
# and any trusted frontend domain.  The wildcard below is acceptable only
# during local development / hackathon demos.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # FIXME: tighten before production deployment
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(router)


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("GreenPipe starting up (env=%s)", settings.app_env)
    if settings.app_env == "development":
        try:
            await create_tables()
            logger.info("Database tables ensured.")
        except Exception as exc:
            logger.warning("Could not create DB tables (no DB configured?): %s", exc)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    logger.info("GreenPipe shutting down.")
