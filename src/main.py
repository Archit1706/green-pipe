"""
GreenPipe FastAPI application entry point.

Built on Green Software Foundation Standards:
- Software Carbon Intensity (SCI) – ISO/IEC 21031:2024
- GSF Carbon Aware SDK
- GSF Impact Framework (Teads curve)
- ECO-CI SPECpower approach
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
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
