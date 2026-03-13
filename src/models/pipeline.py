"""
SQLAlchemy ORM models for GreenPipe.

Schema follows the plan's PostgreSQL design with GSF-attributed column comments.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PipelineRun(Base):
    """
    Stores GSF-compliant carbon metrics for a single GitLab pipeline run.
    """

    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gitlab_pipeline_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    project_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    runner_location: Mapped[str | None] = mapped_column(String(100))
    author_name: Mapped[str | None] = mapped_column(String(255), index=True)

    # -- Energy metrics (GSF Impact Framework: Teads curve + SPECpower) --
    energy_kwh: Mapped[float | None] = mapped_column(Numeric(10, 6))
    energy_methodology: Mapped[str] = mapped_column(
        String(200),
        default="GSF Impact Framework Teads Curve + SPECpower",
    )

    # -- Carbon metrics (GSF Carbon Aware SDK) --
    carbon_intensity_gco2_kwh: Mapped[float | None] = mapped_column(Numeric(10, 4))
    carbon_data_source: Mapped[str | None] = mapped_column(String(200))

    # -- SCI metrics (ISO/IEC 21031:2024) --
    operational_carbon_gco2: Mapped[float | None] = mapped_column(Numeric(12, 6))
    embodied_carbon_gco2: Mapped[float | None] = mapped_column(Numeric(12, 6))
    total_carbon_gco2: Mapped[float | None] = mapped_column(Numeric(12, 6))
    sci_score: Mapped[float | None] = mapped_column(Numeric(12, 6))
    sci_functional_unit: Mapped[str] = mapped_column(String(100), default="pipeline_run")

    # -- NLP urgency classification --
    urgency_classification: Mapped[str | None] = mapped_column(String(50))
    urgency_confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))

    # -- Metadata --
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=_utcnow,
    )

    # Relationships
    jobs: Mapped[list[PipelineJob]] = relationship(
        "PipelineJob",
        back_populates="pipeline_run",
        cascade="all, delete-orphan",
    )
    gsf_compliance_logs: Mapped[list[GSFComplianceLog]] = relationship(
        "GSFComplianceLog",
        back_populates="pipeline_run",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<PipelineRun id={self.id} gitlab_id={self.gitlab_pipeline_id} "
            f"sci={self.sci_score}>"
        )


class PipelineJob(Base):
    """
    Individual job within a pipeline run, with per-job energy metrics.
    """

    __tablename__ = "pipeline_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pipeline_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    gitlab_job_id: Mapped[int | None] = mapped_column(BigInteger)
    job_name: Mapped[str | None] = mapped_column(String(255))
    runner_type: Mapped[str | None] = mapped_column(String(100))
    runner_tags: Mapped[str | None] = mapped_column(String(500))  # comma-separated
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    cpu_utilization_percent: Mapped[float | None] = mapped_column(Numeric(5, 2))

    # Per-job energy (GSF Impact Framework)
    energy_kwh: Mapped[float | None] = mapped_column(Numeric(10, 8))
    runner_tdp_watts: Mapped[float | None] = mapped_column(Numeric(8, 2))
    tdp_factor: Mapped[float | None] = mapped_column(Numeric(6, 4))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=_utcnow,
    )

    pipeline_run: Mapped[PipelineRun] = relationship("PipelineRun", back_populates="jobs")

    def __repr__(self) -> str:
        return f"<PipelineJob id={self.id} name={self.job_name} energy={self.energy_kwh}>"


class GSFComplianceLog(Base):
    """
    Tracks which GSF standards were applied to each pipeline analysis.
    """

    __tablename__ = "gsf_compliance_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pipeline_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    standard_name: Mapped[str] = mapped_column(String(100))       # e.g. 'SCI'
    standard_version: Mapped[str | None] = mapped_column(String(50))  # e.g. 'ISO/IEC 21031:2024'
    compliance_status: Mapped[str] = mapped_column(String(50))    # 'compliant' | 'partial' | 'skipped'
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=_utcnow,
    )

    pipeline_run: Mapped[PipelineRun] = relationship(
        "PipelineRun", back_populates="gsf_compliance_logs"
    )

    def __repr__(self) -> str:
        return (
            f"<GSFComplianceLog standard={self.standard_name} "
            f"status={self.compliance_status}>"
        )


class DeferralAuditRecord(Base):
    """
    Audit log for every auto-deferral decision.

    Records the original carbon intensity, the target low-carbon window,
    predicted savings, urgency class, policy mode, and final action taken.
    Provides reproducible decision logic for judging and compliance.
    """

    __tablename__ = "deferral_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gitlab_pipeline_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    project_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    ref: Mapped[str | None] = mapped_column(String(255))

    # Carbon context
    original_intensity_gco2_kwh: Mapped[float | None] = mapped_column(Numeric(10, 4))
    target_intensity_gco2_kwh: Mapped[float | None] = mapped_column(Numeric(10, 4))
    target_window: Mapped[str | None] = mapped_column(String(100))
    predicted_savings_pct: Mapped[float | None] = mapped_column(Numeric(6, 2))

    # Classification
    urgency_class: Mapped[str | None] = mapped_column(String(50))
    urgency_confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))

    # Policy
    policy_mode: Mapped[str | None] = mapped_column(String(50))
    # Action: "none" | "recommended" | "awaiting_approval" | "deferred" | "force_run"
    action_taken: Mapped[str] = mapped_column(String(50), default="none")
    action_reason: Mapped[str | None] = mapped_column(Text)

    # Outcome
    pipeline_cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    schedule_id: Mapped[int | None] = mapped_column(BigInteger)
    schedule_cron: Mapped[str | None] = mapped_column(String(100))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=_utcnow,
    )

    def __repr__(self) -> str:
        return (
            f"<DeferralAuditRecord pipeline={self.gitlab_pipeline_id} "
            f"action={self.action_taken}>"
        )
