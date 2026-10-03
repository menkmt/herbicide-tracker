"""re-tag sections labelled with a raw "undetermined" agency code

The first land-manager pass printed the SMA layer's UND/UNK codes as if they
were agencies. Clearing those rows lets the next deploy re-tag them with the
corrected labels.

Revision ID: e5b07c3a9d14
Revises: d3f81b6a0c52
Create Date: 2026-10-04 08:00:00
"""
from __future__ import annotations

from alembic import op

revision = "e5b07c3a9d14"
down_revision = "d3f81b6a0c52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE plss_sections SET land_category = NULL, land_label = NULL, land_unit = NULL "
        "WHERE land_category NOT IN ('national_forest', 'blm', 'state', 'local', 'tribal', "
        "'private') OR upper(coalesce(land_unit, '')) IN ('UND', 'UNK')"
    )


def downgrade() -> None:
    pass
