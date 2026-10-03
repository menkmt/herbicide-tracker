"""timestamp defaults on the report-card tables

The report-card migration created created_at/updated_at without the server
default every other table has, so inserts failed. Setting a default is
harmless where it already exists, so this is safe on any server.

Revision ID: d3f81b6a0c52
Revises: c9a4e2f71d35
Create Date: 2026-10-03 21:00:00
"""
from __future__ import annotations

from alembic import op

revision = "d3f81b6a0c52"
down_revision = "c9a4e2f71d35"
branch_labels = None
depends_on = None

TABLES = ("county_officials", "county_records_status", "inspections")


def upgrade() -> None:
    for table in TABLES:
        for column in ("created_at", "updated_at"):
            op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT now()")


def downgrade() -> None:
    for table in TABLES:
        for column in ("created_at", "updated_at"):
            op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT")
