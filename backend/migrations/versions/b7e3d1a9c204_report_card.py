"""county officials, records status, inspections

Revision ID: b7e3d1a9c204
Revises: a41f2c9d7b10
Create Date: 2026-09-29 09:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7e3d1a9c204"
down_revision = "a41f2c9d7b10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "county_officials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("county_id", sa.Integer(), sa.ForeignKey("counties.id"), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=True),
        sa.Column("role", sa.String(length=24), nullable=True),
        sa.Column("started_on", sa.Date(), nullable=True),
        sa.Column("ended_on", sa.Date(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_note", sa.Text(), nullable=True),
        sa.Column("email", sa.String(length=160), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_county_officials_county", "county_officials", ["county_id", "is_current"])

    op.create_table(
        "county_records_status",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("county_id", sa.Integer(), sa.ForeignKey("counties.id"), nullable=False),
        sa.Column("record_kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=True),
        sa.Column("requested_on", sa.Date(), nullable=True),
        sa.Column("received_on", sa.Date(), nullable=True),
        sa.Column("covers_from", sa.Date(), nullable=True),
        sa.Column("covers_to", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("request_reference", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("county_id", "record_kind", name="uq_county_records_kind"),
    )

    op.create_table(
        "inspections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("county_id", sa.Integer(), sa.ForeignKey("counties.id"), nullable=False),
        sa.Column("source_file_id", sa.Integer(), sa.ForeignKey("source_files.id"), nullable=True),
        sa.Column("inspected_on", sa.Date(), nullable=True),
        sa.Column("inspection_type", sa.String(length=32), nullable=True),
        sa.Column("inspection_type_raw", sa.String(length=120), nullable=True),
        sa.Column("document_number", sa.String(length=64), nullable=True),
        sa.Column("site_id", sa.String(length=32), nullable=True),
        sa.Column("mtrs", sa.String(length=16), nullable=True),
        sa.Column("permit_number", sa.String(length=48), nullable=True),
        sa.Column("operator_name", sa.String(length=255), nullable=True),
        sa.Column("applicator_name", sa.String(length=255), nullable=True),
        sa.Column("inspector_name", sa.String(length=160), nullable=True),
        sa.Column("outcome", sa.String(length=24), nullable=True),
        sa.Column("violations_count", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("provenance", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inspections_county_date", "inspections", ["county_id", "inspected_on"])
    op.create_index("ix_inspections_site", "inspections", ["site_id"])


def downgrade() -> None:
    op.drop_index("ix_inspections_site", table_name="inspections")
    op.drop_index("ix_inspections_county_date", table_name="inspections")
    op.drop_table("inspections")
    op.drop_table("county_records_status")
    op.drop_index("ix_county_officials_county", table_name="county_officials")
    op.drop_table("county_officials")
