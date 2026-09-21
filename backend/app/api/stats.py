"""Aggregate statistics, computed in the database.

The in-memory aggregator in :mod:`app.analytics.aggregate` is the reference
implementation and is what the tests exercise; this computes the same figures
in SQL so the API does not have to load every record to answer "how many acres
in Lassen last year".

Quantities keep the same discipline as everywhere else: gallons and pounds are
summed separately and never added together, and a quantity that could not be
resolved to either is reported as an outstanding amount rather than dropped.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Float, and_, cast, func, select
from sqlalchemy.orm import Session

from app.models import (
    ApplicationCluster,
    ClusterRecord,
    County,
    PurProduct,
    PurRecord,
)

_GROUPS = {
    "county": (County.name, "county"),
    "year": (func.extract("year", ApplicationCluster.date_start), "year"),
    "applicator": (PurRecord.applicator_name, "applicator"),
    "landowner": (ApplicationCluster.owner_name, "landowner"),
    "method": (ApplicationCluster.method, "method"),
    "product": (PurProduct.product_name, "product"),
}


def compute_statistics(
    session: Session,
    *,
    county: str | None = None,
    group_by: str = "county",
) -> dict[str, Any]:
    published = ApplicationCluster.status == "published"

    # --- headline totals --------------------------------------------------
    base = select(
        func.count(ApplicationCluster.id),
        func.coalesce(func.sum(ApplicationCluster.total_acres), 0.0),
        func.count(ApplicationCluster.id).filter(ApplicationCluster.method == "aerial"),
        func.count(ApplicationCluster.id).filter(ApplicationCluster.method == "ground"),
        func.min(ApplicationCluster.date_start),
        func.max(ApplicationCluster.date_end),
    ).where(published)
    if county:
        base = base.join(County, ApplicationCluster.county_id == County.id).where(
            County.slug == county
        )
    applications, acres, aerial, ground, first, last = session.execute(base).one()

    # --- chemical totals --------------------------------------------------
    quantities = select(
        PurProduct.product_name,
        func.coalesce(func.sum(cast(PurProduct.gallons, Float)), 0.0),
        func.coalesce(func.sum(cast(PurProduct.pounds, Float)), 0.0),
        func.count(PurProduct.id),
        func.count(PurProduct.id).filter(
            and_(PurProduct.gallons.is_(None), PurProduct.pounds.is_(None))
        ),
    ).join(
        ClusterRecord, ClusterRecord.record_id == PurProduct.record_id
    ).join(
        ApplicationCluster, ApplicationCluster.id == ClusterRecord.cluster_id
    ).where(published)
    if county:
        quantities = quantities.join(
            County, ApplicationCluster.county_id == County.id
        ).where(County.slug == county)
    quantities = quantities.group_by(PurProduct.product_name).order_by(
        func.sum(cast(PurProduct.gallons, Float)).desc().nullslast()
    )

    chemicals = [
        {
            "product": name,
            "gallons": round(float(gallons), 2) if gallons else None,
            "pounds": round(float(pounds), 2) if pounds else None,
            "lines": lines,
            # Lines whose unit could not be resolved; reported so a total is
            # never quietly incomplete.
            "unresolved_lines": unresolved,
        }
        for name, gallons, pounds, lines, unresolved in session.execute(quantities).all()
    ]

    # --- grouped breakdown ------------------------------------------------
    column, label = _GROUPS.get(group_by, _GROUPS["county"])
    grouped = select(
        column,
        func.count(func.distinct(ApplicationCluster.id)),
        func.coalesce(func.sum(func.distinct(ApplicationCluster.total_acres)), 0.0),
    ).where(published)

    if group_by == "county":
        grouped = grouped.join(County, ApplicationCluster.county_id == County.id)
    elif group_by in {"applicator", "product"}:
        grouped = grouped.join(
            ClusterRecord, ClusterRecord.cluster_id == ApplicationCluster.id
        ).join(PurRecord, PurRecord.id == ClusterRecord.record_id)
        if group_by == "product":
            grouped = grouped.join(PurProduct, PurProduct.record_id == PurRecord.id)
    if county and group_by != "county":
        grouped = grouped.join(County, ApplicationCluster.county_id == County.id).where(
            County.slug == county
        )

    grouped = grouped.group_by(column).order_by(func.count(func.distinct(ApplicationCluster.id)).desc())

    breakdown = [
        {
            label: str(key) if key is not None else "(not reported)",
            "applications": count,
            "acres": round(float(group_acres), 2) if group_acres else 0.0,
        }
        for key, count, group_acres in session.execute(grouped.limit(100)).all()
    ]

    return {
        "scope": {"county": county or "all counties"},
        "totals": {
            "applications": applications,
            "acres": round(float(acres), 2),
            "aerial_applications": aerial,
            "ground_applications": ground,
            "aerial_share": round(aerial / (aerial + ground), 4) if (aerial + ground) else None,
            "first_date": first.isoformat() if first else None,
            "last_date": last.isoformat() if last else None,
        },
        "chemicals": chemicals,
        "group_by": group_by,
        "breakdown": breakdown,
    }
