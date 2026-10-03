"""monthly web search counter

Revision ID: b8e14f2d6a07
Revises: a7d93e4c1f68
Create Date: 2026-10-04 13:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8e14f2d6a07"
down_revision = "a7d93e4c1f68"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_usage",
        sa.Column("month", sa.String(length=7), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("month"),
    )


def downgrade() -> None:
    op.drop_table("search_usage")
