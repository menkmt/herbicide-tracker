"""Section outlines for applications whose parcels are not matched yet.

A use report places an application in a public-land-survey section, one
square mile. Until the property inside it is identified, the section is the
most honest thing to draw: it is exactly what the county was told, and the
map labels it as the reported square mile rather than the sprayed area.

Geometry comes from the BLM's national PLSS service, once per section, and is
cached in ``plss_sections`` for good.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.siteid import SiteIdDecodeError, parse_mtrs
from app.models import ApplicationCluster, ClusterRecord, PlssSection, PurRecord
from app.providers.landowner import LandManagerError, LandManagerProvider
from app.providers.plss import PlssError, PlssProvider

logger = logging.getLogger(__name__)


@dataclass
class SectionReport:
    sections_needed: int = 0
    fetched: int = 0
    not_found: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    records_linked: int = 0
    clusters_located: int = 0
    land_tagged: int = 0

    def render(self) -> str:
        lines = [
            f"sections needing an outline: {self.sections_needed}",
            f"fetched from the PLSS service: {self.fetched}",
            f"records linked to a section: {self.records_linked}",
            f"applications given a location: {self.clusters_located}",
            f"sections tagged with their land manager: {self.land_tagged}",
        ]
        if self.not_found:
            lines.append(f"not found in the PLSS service: {', '.join(self.not_found[:20])}")
        if self.errors:
            lines.append(f"errors: {len(self.errors)} (first: {self.errors[0]})")
        return "\n".join(lines)


def ensure_section_geometry(
    session: Session,
    provider: PlssProvider | None = None,
    *,
    limit: int = 500,
    land: LandManagerProvider | None = None,
) -> SectionReport:
    """Fetch and cache outlines for every section a record mentions, then give
    applications without parcel geometry their sections' outline."""
    provider = provider or PlssProvider()
    report = SectionReport()

    have = {
        m for (m,) in session.execute(
            select(PlssSection.mtrs).where(PlssSection.geom.is_not(None))
        )
    }
    wanted = {
        m for (m,) in session.execute(
            select(PurRecord.mtrs).where(PurRecord.mtrs.is_not(None)).distinct()
        )
    }
    missing = sorted(wanted - have)
    report.sections_needed = len(missing)

    for mtrs in missing[:limit]:
        try:
            decoded = parse_mtrs(mtrs, repair_ocr=False)
        except SiteIdDecodeError as exc:
            report.errors.append(f"{mtrs}: {exc}")
            continue
        try:
            found = provider.section(decoded)
        except PlssError as exc:
            report.errors.append(str(exc))
            # One failure usually means the service is unreachable; do not
            # hammer it for every remaining section.
            if len(report.errors) >= 3:
                break
            continue
        if found is None or not found.geometry:
            report.not_found.append(mtrs)
            continue
        row = session.scalar(select(PlssSection).where(PlssSection.mtrs == mtrs))
        if row is None:
            row = PlssSection(
                mtrs=mtrs, meridian=decoded.meridian, township=decoded.township,
                township_dir=decoded.township_dir, range=decoded.range,
                range_dir=decoded.range_dir, section=decoded.section,
            )
            session.add(row)
        row.geom = func.ST_Multi(
            func.ST_SetSRID(func.ST_GeomFromGeoJSON(json.dumps(found.geometry)), 4326)
        )
        row.source = found.source
        row.retrieved_at = func.now()
        session.flush()
        report.fetched += 1

    # Link records to their section rows.
    result = session.execute(
        text(
            "UPDATE pur_records r SET plss_section_id = s.id FROM plss_sections s "
            "WHERE r.mtrs = s.mtrs AND r.plss_section_id IS NULL AND s.geom IS NOT NULL"
        )
    )
    report.records_linked = result.rowcount or 0

    # Applications with no parcel geometry take the union of their sections,
    # so radius search and the map can place them. Marked so it is never
    # mistaken for a parcel outline.
    unlocated = session.scalars(
        select(ApplicationCluster).where(ApplicationCluster.geom.is_(None))
    ).all()
    for cluster in unlocated:
        union = session.scalar(
            select(func.ST_Multi(func.ST_Union(PlssSection.geom)))
            .select_from(ClusterRecord)
            .join(PurRecord, PurRecord.id == ClusterRecord.record_id)
            .join(PlssSection, PlssSection.id == PurRecord.plss_section_id)
            .where(ClusterRecord.cluster_id == cluster.id, PlssSection.geom.is_not(None))
        )
        if union is None:
            continue
        cluster.geom = union
        scoring = dict(cluster.scoring or {})
        scoring["geom_basis"] = "plss_section"
        cluster.scoring = scoring
        report.clusters_located += 1

    # Who manages each section's land, at its centre: national forest,
    # BLM, state or private. Best effort; a failure leaves it unlabelled.
    land = land or LandManagerProvider()
    untagged = session.execute(
        select(PlssSection.id, func.ST_X(func.ST_Centroid(PlssSection.geom)),
               func.ST_Y(func.ST_Centroid(PlssSection.geom)))
        .where(PlssSection.geom.is_not(None), PlssSection.land_category.is_(None))
        .limit(limit)
    ).all()
    failures = 0
    for section_id, lon, lat in untagged:
        try:
            manager = land.at(lon, lat)
        except LandManagerError as exc:
            report.errors.append(str(exc))
            failures += 1
            if failures >= 3:
                break
            continue
        row = session.get(PlssSection, section_id)
        row.land_category = manager.category
        row.land_label = manager.label
        row.land_unit = manager.unit
        report.land_tagged += 1

    session.commit()
    return report
