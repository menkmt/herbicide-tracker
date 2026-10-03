"""company contact lookup fields

Revision ID: f2c6a8e1b937
Revises: e5b07c3a9d14
Create Date: 2026-10-04 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f2c6a8e1b937"
down_revision = "e5b07c3a9d14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("phone_source", sa.String(length=160), nullable=True))
    op.add_column("companies", sa.Column("contact_source_url", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("contact_evidence", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("contact_checked_at", sa.DateTime(timezone=True),
                                         nullable=True))


def downgrade() -> None:
    for column in ("contact_checked_at", "contact_evidence", "contact_source_url", "phone_source"):
        op.drop_column("companies", column)
