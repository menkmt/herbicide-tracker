"""County report cards.

Reported activity against reported oversight, county by county: how many
applications, sites and acres the county was told about, and how many of
them the county says it inspected. Both numbers come from the county's own
records. The grade is the publisher's, and says so.
"""

from __future__ import annotations

import hashlib
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import rate_limit, require_admin
from app.core.access import Principal
from app.db import get_session
from app.models import (
    ApplicationCluster,
    ClusterRecord,
    County,
    CountyOfficial,
    CountyRecordsStatus,
    Inspection,
    PurRecord,
    SourceFile,
)
from app.pipeline.storage import get_storage
from app.reportcard.grading import (
    GRADE_THRESHOLDS,
    YearMetrics,
    grade,
    thresholds_text,
)
from app.reportcard.inspections_csv import read_inspections

router = APIRouter(prefix="/api", tags=["report-card"])

PUBLISHED = ApplicationCluster.status == "published"
#: Days either side of an application's dates within which a use-monitoring
#: inspection at the same site counts as an inspection of it.
MATCH_WINDOW = timedelta(days=1)
RECORD_KINDS = ("inspections", "use_reports", "notices_of_intent", "permits", "enforcement")
STATUSES = ("not_requested", "requested", "partial", "received", "county_reports_none", "refused")


# ---------------------------------------------------------------------------
# Computing a county's card
# ---------------------------------------------------------------------------

def _priority(cluster: ApplicationCluster) -> bool:
    flags = cluster.flags or {}
    return bool(flags.get("has_regulatory_restriction")) or cluster.method == "aerial"


def _records_status(session: Session, county_id: int) -> dict[str, dict]:
    rows = session.scalars(
        select(CountyRecordsStatus).where(CountyRecordsStatus.county_id == county_id)
    ).all()
    by_kind = {r.record_kind: r for r in rows}
    out = {}
    for kind in RECORD_KINDS:
        r = by_kind.get(kind)
        out[kind] = {
            "status": r.status if r else "not_requested",
            "requested_on": r.requested_on.isoformat() if r and r.requested_on else None,
            "received_on": r.received_on.isoformat() if r and r.received_on else None,
            "covers_from": r.covers_from.isoformat() if r and r.covers_from else None,
            "covers_to": r.covers_to.isoformat() if r and r.covers_to else None,
            "note": r.note if r else None,
        }
    return out


def _officials(session: Session, county_id: int) -> dict:
    rows = session.scalars(
        select(CountyOfficial)
        .where(CountyOfficial.county_id == county_id)
        .order_by(CountyOfficial.is_current.desc(), CountyOfficial.started_on.desc().nullslast())
    ).all()

    def one(o: CountyOfficial) -> dict:
        return {
            "name": o.name,
            "title": o.title,
            "role": o.role,
            "started_on": o.started_on.isoformat() if o.started_on else None,
            "ended_on": o.ended_on.isoformat() if o.ended_on else None,
            "is_current": o.is_current,
            "as_of": o.as_of.isoformat() if o.as_of else None,
            "source_url": o.source_url,
            "source_note": o.source_note,
            "email": o.email,
            "phone": o.phone,
        }

    current = next((o for o in rows if o.is_current and o.role == "commissioner"), None)
    return {
        "commissioner": one(current) if current else None,
        "history": [one(o) for o in rows if o is not current],
    }


def _year_metrics(session: Session, county: County) -> list[YearMetrics]:
    clusters = session.scalars(
        select(ApplicationCluster).where(
            ApplicationCluster.county_id == county.id, PUBLISHED,
            ApplicationCluster.is_planned.is_(False),
        )
    ).all()
    if not clusters:
        return []

    cluster_ids = [c.id for c in clusters]
    site_rows = session.execute(
        select(ClusterRecord.cluster_id, PurRecord.site_id)
        .join(PurRecord, PurRecord.id == ClusterRecord.record_id)
        .where(ClusterRecord.cluster_id.in_(cluster_ids), PurRecord.site_id.is_not(None))
    ).all()
    sites_by_cluster: dict[int, set[str]] = {}
    for cluster_id, site_id in site_rows:
        sites_by_cluster.setdefault(cluster_id, set()).add(site_id.replace(" ", ""))

    inspections = session.scalars(
        select(Inspection).where(Inspection.county_id == county.id)
    ).all()
    use_monitoring = [
        i for i in inspections if i.inspection_type == "use_monitoring" and i.inspected_on
    ]

    def inspected(cluster: ApplicationCluster) -> bool:
        sites = sites_by_cluster.get(cluster.id, set())
        if not sites or not cluster.date_start:
            return False
        start = cluster.date_start - MATCH_WINDOW
        end = (cluster.date_end or cluster.date_start) + MATCH_WINDOW
        return any(
            i.site_id and i.site_id.replace(" ", "") in sites and start <= i.inspected_on <= end
            for i in use_monitoring
        )

    years: dict[int, dict] = {}
    for c in clusters:
        if not c.date_start:
            continue
        y = years.setdefault(c.date_start.year, {
            "applications": 0, "priority": 0, "acres": 0.0, "sites": set(),
            "priority_inspected": 0,
        })
        y["applications"] += 1
        y["acres"] += float(c.total_acres or 0)
        y["sites"] |= sites_by_cluster.get(c.id, set())
        if _priority(c):
            y["priority"] += 1
            if inspected(c):
                y["priority_inspected"] += 1

    for i in inspections:
        if not i.inspected_on:
            continue
        y = years.setdefault(i.inspected_on.year, {
            "applications": 0, "priority": 0, "acres": 0.0, "sites": set(),
            "priority_inspected": 0,
        })
        y["inspections"] = y.get("inspections", 0) + 1
        if i.inspection_type == "use_monitoring":
            y["use_monitoring"] = y.get("use_monitoring", 0) + 1
        if i.outcome == "violation":
            y["violations"] = y.get("violations", 0) + max(1, i.violations_count or 1)

    return [
        YearMetrics(
            year=year,
            applications=v["applications"],
            priority_applications=v["priority"],
            acres=round(v["acres"], 1),
            distinct_sites=len(v["sites"]),
            inspections=v.get("inspections", 0),
            use_monitoring_inspections=v.get("use_monitoring", 0),
            priority_inspected=v["priority_inspected"],
            violations_found=v.get("violations", 0),
        )
        for year, v in sorted(years.items(), reverse=True)
    ]


def build_report_card(session: Session, county: County) -> dict:
    status = _records_status(session, county.id)
    inspection_status = status["inspections"]["status"]
    years = _year_metrics(session, county)
    graded = [
        {
            **m.__dict__,
            "coverage": m.coverage,
            "grade": grade(m, inspection_status).to_dict(),
        }
        for m in years
    ]
    # The headline grade is the latest complete year with anything to grade.
    headline = next((g for g in graded if g["grade"]["letter"]), None)
    totals = {
        "applications": sum(m.applications for m in years),
        "priority_applications": sum(m.priority_applications for m in years),
        "acres": round(sum(m.acres for m in years), 1),
        "inspections": sum(m.inspections for m in years),
        "use_monitoring_inspections": sum(m.use_monitoring_inspections for m in years),
        "priority_inspected": sum(m.priority_inspected for m in years),
        "violations_found": sum(m.violations_found for m in years),
    }
    return {
        "county": {"name": county.name, "slug": county.slug},
        "officials": _officials(session, county.id),
        "records": status,
        "headline_grade": (
            headline["grade"] if headline else grade(None, inspection_status).to_dict()
        ),
        "headline_year": headline["year"] if headline else None,
        "years": graded,
        "totals": totals,
        "rubric": {
            "priority": "Applications of California restricted materials, and aerial applications.",
            "matched": (
                "A priority application counts as inspected when the county recorded a "
                "use-monitoring inspection at the same site ID within a day of its dates."
            ),
            "thresholds": thresholds_text(),
            "grades": [{"letter": letter, "min_coverage": t} for letter, t in GRADE_THRESHOLDS],
            "who": "The grade is this publisher's editorial rating, not a regulatory finding.",
        },
    }


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

@router.get("/counties/{slug}/report-card")
def county_report_card(
    slug: str, session: Session = Depends(get_session), _: Principal = Depends(rate_limit)
) -> dict:
    county = session.scalar(select(County).where(County.slug == slug))
    if county is None:
        raise HTTPException(404, "County not found")
    return build_report_card(session, county)


@router.get("/report-card")
def statewide_report_card(
    session: Session = Depends(get_session), _: Principal = Depends(rate_limit)
) -> dict:
    """Every county with anything to show, ranked worst first."""
    counties = session.scalars(select(County).order_by(County.name)).all()
    cards = []
    for county in counties:
        card = build_report_card(session, county)
        if card["totals"]["applications"] == 0 and not card["officials"]["commissioner"]:
            continue
        cards.append({
            "county": card["county"],
            "commissioner": card["officials"]["commissioner"],
            "records": card["records"]["inspections"],
            "headline_grade": card["headline_grade"],
            "headline_year": card["headline_year"],
            "totals": card["totals"],
        })
    order = {"F": 0, "D": 1, "C": 2, "B": 3, "A": 4, None: 5}
    cards.sort(key=lambda c: (order.get(c["headline_grade"]["letter"], 5), c["county"]["name"]))
    return {"counties": cards, "rubric_thresholds": thresholds_text()}


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

class OfficialIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    title: str = "Agricultural Commissioner"
    role: str = "commissioner"
    started_on: date | None = None
    as_of: date | None = None
    source_url: str | None = None
    source_note: str | None = None
    email: str | None = None
    phone: str | None = None


def _county_or_404(session: Session, slug: str) -> County:
    county = session.scalar(select(County).where(County.slug == slug))
    if county is None:
        # A county page exists for every California county; the row is
        # created the first time it is written to.
        from app.pipeline.ingest import get_or_create_county

        county = get_or_create_county(session, slug.replace("-", " ").title())
        if county is None:
            raise HTTPException(404, "County not found")
    return county


@router.post("/admin/counties/{slug}/official")
def set_official(
    slug: str,
    body: OfficialIn,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_admin),
) -> dict:
    """Record the current commissioner. The previous one becomes history."""
    county = _county_or_404(session, slug)
    today = datetime.now(UTC).date()
    if body.role == "commissioner":
        for previous in session.scalars(
            select(CountyOfficial).where(
                CountyOfficial.county_id == county.id,
                CountyOfficial.role == "commissioner",
                CountyOfficial.is_current.is_(True),
            )
        ):
            if previous.name.strip().lower() == body.name.strip().lower():
                previous.as_of = body.as_of or today
                previous.source_url = body.source_url or previous.source_url
                previous.email = body.email or previous.email
                previous.phone = body.phone or previous.phone
                session.commit()
                return {"updated": previous.id}
            previous.is_current = False
            previous.ended_on = previous.ended_on or body.started_on or today
    official = CountyOfficial(
        county_id=county.id, name=body.name.strip(), title=body.title, role=body.role,
        started_on=body.started_on, as_of=body.as_of or today, source_url=body.source_url,
        source_note=body.source_note, email=body.email, phone=body.phone, is_current=True,
    )
    session.add(official)
    session.commit()
    return {"created": official.id}


class RecordsStatusIn(BaseModel):
    record_kind: str
    status: str
    requested_on: date | None = None
    received_on: date | None = None
    covers_from: date | None = None
    covers_to: date | None = None
    note: str | None = None
    request_reference: str | None = None


@router.post("/admin/counties/{slug}/records-status")
def set_records_status(
    slug: str,
    body: RecordsStatusIn,
    session: Session = Depends(get_session),
    _: Principal = Depends(require_admin),
) -> dict:
    if body.record_kind not in RECORD_KINDS:
        raise HTTPException(400, f"record_kind must be one of {', '.join(RECORD_KINDS)}")
    if body.status not in STATUSES:
        raise HTTPException(400, f"status must be one of {', '.join(STATUSES)}")
    county = _county_or_404(session, slug)
    row = session.scalar(
        select(CountyRecordsStatus).where(
            CountyRecordsStatus.county_id == county.id,
            CountyRecordsStatus.record_kind == body.record_kind,
        )
    )
    if row is None:
        row = CountyRecordsStatus(county_id=county.id, record_kind=body.record_kind)
        session.add(row)
    for name, value in body.model_dump().items():
        setattr(row, name, value)
    session.commit()
    return {"ok": True}


@router.post("/admin/counties/{slug}/inspections/import")
async def import_inspections(
    slug: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    _: Principal = Depends(require_admin),
) -> dict:
    """Load a county's inspection log (CSV or tab-separated export)."""
    county = _county_or_404(session, slug)
    content = await file.read()
    if not content:
        raise HTTPException(400, "The file is empty")
    result = read_inspections(content)
    if not result.rows:
        raise HTTPException(400, "No inspection rows could be read: " + "; ".join(result.problems))

    # The log is a source document like any other: kept, hashed, citable.
    digest = hashlib.sha256(content).hexdigest()
    source = session.scalar(select(SourceFile).where(SourceFile.sha256 == digest))
    if source is None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / Path(file.filename or "inspections.csv").name
            path.write_bytes(content)
            storage_key = get_storage().put(path, sha256=digest, filename=path.name)
        source = SourceFile(
            filename=Path(file.filename or "inspections.csv").name,
            sha256=digest,
            byte_size=len(content),
            storage_mode="local",
            storage_key=storage_key,
            document_kind="inspection_log",
            origin="upload",
            processing_state="processed",
            received_at=datetime.now(UTC),
            county_id=county.id,
        )
        session.add(source)
        session.flush()

    added = 0
    for row in result.rows:
        exists = session.scalar(
            select(func.count()).select_from(Inspection).where(
                Inspection.county_id == county.id,
                Inspection.inspected_on == row.inspected_on,
                Inspection.inspection_type == row.inspection_type,
                Inspection.site_id == row.site_id,
                Inspection.document_number == row.document_number,
                Inspection.operator_name == row.operator_name,
            )
        )
        if exists:
            continue
        session.add(
            Inspection(
                county_id=county.id,
                source_file_id=source.id if source else None,
                inspected_on=row.inspected_on,
                inspection_type=row.inspection_type,
                inspection_type_raw=row.inspection_type_raw,
                document_number=row.document_number,
                site_id=row.site_id,
                mtrs=row.mtrs,
                permit_number=row.permit_number,
                operator_name=row.operator_name,
                applicator_name=row.applicator_name,
                inspector_name=row.inspector_name,
                outcome=row.outcome,
                violations_count=row.violations_count,
                notes=row.notes,
                provenance={"line": row.line, "extra": row.extra},
            )
        )
        added += 1

    # Loading a log is evidence the records arrived; record that unless a
    # person has already said something more specific.
    status = session.scalar(
        select(CountyRecordsStatus).where(
            CountyRecordsStatus.county_id == county.id,
            CountyRecordsStatus.record_kind == "inspections",
        )
    )
    if status is None:
        status = CountyRecordsStatus(county_id=county.id, record_kind="inspections")
        session.add(status)
    if status.status in ("not_requested", "requested"):
        status.status = "received"
        status.received_on = datetime.now(UTC).date()
    dates = [r.inspected_on for r in result.rows if r.inspected_on]
    if dates:
        lo, hi = min(dates), max(dates)
        status.covers_from = lo if not status.covers_from else min(status.covers_from, lo)
        status.covers_to = hi if not status.covers_to else max(status.covers_to, hi)
    session.commit()

    return {
        "rows_read": len(result.rows),
        "added": added,
        "duplicates": len(result.rows) - added,
        "columns_matched": result.columns_matched,
        "columns_unmatched": result.columns_unmatched,
        "problems": result.problems,
    }


@router.get("/admin/counties/{slug}/report-card")
def admin_report_card(
    slug: str, session: Session = Depends(get_session), _: Principal = Depends(require_admin)
) -> dict:
    county = _county_or_404(session, slug)
    return build_report_card(session, county)
