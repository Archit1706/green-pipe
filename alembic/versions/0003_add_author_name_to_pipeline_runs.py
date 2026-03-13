"""Add author_name column to pipeline_runs for leaderboard feature

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-13
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "pipeline_runs",
        sa.Column("author_name", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_pipeline_runs_author_name",
        "pipeline_runs",
        ["author_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_runs_author_name", table_name="pipeline_runs")
    op.drop_column("pipeline_runs", "author_name")
