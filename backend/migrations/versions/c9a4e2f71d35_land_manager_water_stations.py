"""land manager on sections, water monitoring stations

Revision ID: c9a4e2f71d35
Revises: b7e3d1a9c204
Create Date: 2026-10-03 20:30:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9a4e2f71d35"
down_revision = "b7e3d1a9c204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plss_sections", sa.Column("land_category", sa.String(length=24), nullable=True))
    op.add_column("plss_sections", sa.Column("land_label", sa.String(length=120), nullable=True))
    op.add_column("plss_sections", sa.Column("land_unit", sa.String(length=160), nullable=True))
    op.create_table(
        "water_stations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("operator", sa.String(length=200), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("location_is_approximate", sa.Boolean(), nullable=False),
        sa.Column("herbicides_tested", sa.Boolean(), nullable=True),
        sa.Column("analytes_note", sa.Text(), nullable=True),
        sa.Column("last_sampled", sa.Date(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("county_id", sa.Integer(), sa.ForeignKey("counties.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "operator", name="uq_water_station"),
    )


def downgrade() -> None:
    op.drop_table("water_stations")
    op.drop_column("plss_sections", "land_unit")
    op.drop_column("plss_sections", "land_label")
    op.drop_column("plss_sections", "land_category")
