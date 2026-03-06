"""Add deferral_audit_log table for auto-deferral decision tracking

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deferral_audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("gitlab_pipeline_id", sa.BigInteger(), index=True),
        sa.Column("project_id", sa.BigInteger(), index=True),
        sa.Column("ref", sa.String(255)),
        # Carbon context
        sa.Column("original_intensity_gco2_kwh", sa.Numeric(10, 4)),
        sa.Column("target_intensity_gco2_kwh", sa.Numeric(10, 4)),
        sa.Column("target_window", sa.String(100)),
        sa.Column("predicted_savings_pct", sa.Numeric(6, 2)),
        # Classification
        sa.Column("urgency_class", sa.String(50)),
        sa.Column("urgency_confidence", sa.Numeric(5, 4)),
        # Policy
        sa.Column("policy_mode", sa.String(50)),
        sa.Column("action_taken", sa.String(50), nullable=False, server_default="none"),
        sa.Column("action_reason", sa.Text()),
        # Outcome
        sa.Column("pipeline_cancelled", sa.Boolean(), server_default="false"),
        sa.Column("schedule_id", sa.BigInteger()),
        sa.Column("schedule_cron", sa.String(100)),
        # Metadata
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("deferral_audit_log")
