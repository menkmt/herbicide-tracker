"""page views

Revision ID: a41f2c9d7b10
Revises: c612d6b34789
Create Date: 2026-09-24 06:10:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a41f2c9d7b10"
down_revision = "c612d6b34789"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "page_views",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("referrer_host", sa.String(length=160), nullable=True),
        sa.Column("visitor", sa.String(length=32), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_page_views_day", "page_views", ["day"])
    op.create_index("ix_page_views_day_path", "page_views", ["day", "path"])


def downgrade() -> None:
    op.drop_index("ix_page_views_day_path", table_name="page_views")
    op.drop_index("ix_page_views_day", table_name="page_views")
    op.drop_table("page_views")
