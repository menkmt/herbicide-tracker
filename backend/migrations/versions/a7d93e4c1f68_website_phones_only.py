"""show only website-sourced phone numbers for companies

Company phone numbers copied from permit contact lists are cleared; the
public phone now comes from the company's own website. Permit numbers remain
in permit_contacts, where they are used to confirm a website is the right
business.

Revision ID: a7d93e4c1f68
Revises: f2c6a8e1b937
Create Date: 2026-10-04 12:00:00
"""
from __future__ import annotations

from alembic import op

revision = "a7d93e4c1f68"
down_revision = "f2c6a8e1b937"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE companies SET phone = NULL, phone_source = NULL "
        "WHERE phone_source LIKE 'permit%' OR (phone IS NOT NULL AND phone_source IS NULL)"
    )


def downgrade() -> None:
    pass
