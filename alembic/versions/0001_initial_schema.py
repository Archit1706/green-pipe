"""Initial schema — pipeline_runs, pipeline_jobs, gsf_compliance_log

Revision ID: 0001
Revises:
Create Date: 2026-03-01
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("gitlab_pipeline_id", sa.BigInteger(), nullable=True),
        sa.Column("project_id", sa.BigInteger(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("runner_location", sa.String(100), nullable=True),

        # Energy (GSF Impact Framework)
        sa.Column("energy_kwh", sa.Numeric(10, 6), nullable=True),
        sa.Column(
            "energy_methodology",
            sa.String(200),
            nullable=False,
            server_default="GSF Impact Framework Teads Curve + SPECpower",
        ),

        # Carbon (GSF Carbon Aware SDK)
        sa.Column("carbon_intensity_gco2_kwh", sa.Numeric(10, 4), nullable=True),
        sa.Column("carbon_data_source", sa.String(200), nullable=True),

        # SCI (ISO/IEC 21031:2024)
        sa.Column("operational_carbon_gco2", sa.Numeric(12, 6), nullable=True),
        sa.Column("embodied_carbon_gco2", sa.Numeric(12, 6), nullable=True),
        sa.Column("total_carbon_gco2", sa.Numeric(12, 6), nullable=True),
        sa.Column("sci_score", sa.Numeric(12, 6), nullable=True),
        sa.Column(
            "sci_functional_unit",
            sa.String(100),
            nullable=False,
            server_default="pipeline_run",
        ),

        # NLP urgency
        sa.Column("urgency_classification", sa.String(50), nullable=True),
        sa.Column("urgency_confidence", sa.Numeric(5, 4), nullable=True),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_pipeline_runs_gitlab_pipeline_id", "pipeline_runs", ["gitlab_pipeline_id"], unique=True)
    op.create_index("ix_pipeline_runs_project_id", "pipeline_runs", ["project_id"])

    op.create_table(
        "pipeline_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "pipeline_run_id",
            sa.Integer(),
            sa.ForeignKey("pipeline_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("gitlab_job_id", sa.BigInteger(), nullable=True),
        sa.Column("job_name", sa.String(255), nullable=True),
        sa.Column("runner_type", sa.String(100), nullable=True),
        sa.Column("runner_tags", sa.String(500), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("cpu_utilization_percent", sa.Numeric(5, 2), nullable=True),

        # Per-job energy (GSF Impact Framework)
        sa.Column("energy_kwh", sa.Numeric(10, 8), nullable=True),
        sa.Column("runner_tdp_watts", sa.Numeric(8, 2), nullable=True),
        sa.Column("tdp_factor", sa.Numeric(6, 4), nullable=True),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_pipeline_jobs_pipeline_run_id", "pipeline_jobs", ["pipeline_run_id"])

    op.create_table(
        "gsf_compliance_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "pipeline_run_id",
            sa.Integer(),
            sa.ForeignKey("pipeline_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("standard_name", sa.String(100), nullable=False),
        sa.Column("standard_version", sa.String(50), nullable=True),
        sa.Column("compliance_status", sa.String(50), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_gsf_compliance_log_pipeline_run_id", "gsf_compliance_log", ["pipeline_run_id"])


def downgrade() -> None:
    op.drop_table("gsf_compliance_log")
    op.drop_table("pipeline_jobs")
    op.drop_table("pipeline_runs")
