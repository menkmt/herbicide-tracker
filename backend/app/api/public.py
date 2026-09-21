"""Public API: the application grid, detail pages, chemicals and search.

Only *published* applications are visible here.  Everything else — records in
review, out-of-coverage records, non-forestry sites — is queryable only by an
administrator, so the public site can never show something a person has not
approved.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import rate_limit, require_capability
from app.config import Settings, get_settings
from app.core.access import Capability, Principal, clamp_page_size
from app.core.coverage import DocumentKind
from app.db import get_session
from app.models import (
    ActiveIngredient,
    ApplicationCluster,
    ClusterParcel,
    ClusterRecord,
    Company,
    County,
    Parcel,
    Person,
    Product,
    ProductIngredient,
    PurProduct,
    PurRecord,
)

router = APIRouter(prefix="/api", tags=["public"])

PUBLISHED = ApplicationCluster.status == "published"


def _cluster_summary(session: Session, cluster: ApplicationCluster) -> dict[str, Any]:
    """The row shape the public grid renders."""
    member_ids = [
        row.record_id
        for row in session.scalars(
            select(ClusterRecord).where(ClusterRecord.cluster_id == cluster.id)
        ).all()
    ]
    products = (
        session.scalars(
            select(PurProduct.product_name)
            .where(PurProduct.record_id.in_(member_ids))
            .distinct()
        ).all()
        if member_ids
        else []
    )
    county = session.get(County, cluster.county_id) if cluster.county_id else None
    flags = cluster.flags or {}
    return {
        "slug": cluster.slug,
        "title": cluster.title,
        "title_basis": cluster.title_basis,
        "owner": cluster.owner_name,
        "county": county.name if county else None,
        "county_slug": county.slug if county else None,
        "date_start": cluster.date_start.isoformat() if cluster.date_start else None,
        "date_end": cluster.date_end.isoformat() if cluster.date_end else None,
        "acres": cluster.total_acres,
        "acreage_is_partial": cluster.acreage_is_partial,
        "method": cluster.method,
        "is_planned": cluster.is_planned,
        "record_count": len(member_ids),
        "chemicals": sorted(p for p in products if p),
        "flag_level": flags.get("highest_level"),
        "flag_headline": flags.get("headline"),
        "has_regulatory_restriction": flags.get("has_regulatory_restriction", False),
        "has_watchlist_entry": flags.get("has_watchlist_entry", False),
        "url": f"/herbicide-application/{cluster.slug}/",
    }


@router.get("/counties")
def list_counties(session: Session = Depends(get_session), _: Principal = Depends(rate_limit)):
    """Counties with published applications, for the county browser."""
    rows = session.execute(
        select(
            County.name,
            County.slug,
            func.count(ApplicationCluster.id),
            func.min(ApplicationCluster.date_start),
            func.max(ApplicationCluster.date_end),
            func.sum(ApplicationCluster.total_acres),
        )
        .join(ApplicationCluster, ApplicationCluster.county_id == County.id)
        .where(PUBLISHED)
        .group_by(County.name, County.slug)
        .order_by(County.name)
    ).all()
    return {
        "counties": [
            {
                "name": name,
                "slug": slug,
                "applications": count,
                "first_date": first.isoformat() if first else None,
                "last_date": last.isoformat() if last else None,
                "acres": float(acres) if acres else 0.0,
                "url": f"/herbicide-tracker/{slug}/",
            }
            for name, slug, count, first, last, acres in rows
        ]
    }


@router.get("/applications")
def list_applications(
    session: Session = Depends(get_session),
    principal: Principal = Depends(rate_limit),
    settings: Settings = Depends(get_settings),
    county: str | None = Query(None, description="County slug"),
    year: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    chemical: str | None = Query(None, description="Product or active ingredient"),
    landowner: str | None = None,
    applicator: str | None = None,
    method: str | None = Query(None, pattern="^(aerial|ground)$"),
    flagged: bool | None = Query(None, description="Only restricted/watchlist applications"),
    kind: str | None = Query(None, pattern="^(use_report|notice_of_intent)$"),
    q: str | None = Query(None, description="Free-text search"),
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1),
):
    """The public application grid, filtered."""
    size = clamp_page_size(
        principal,
        page_size,
        public=settings.anonymous_max_page_size,
        subscriber=settings.subscriber_max_page_size,
    )

    stmt = select(ApplicationCluster).where(PUBLISHED)

    if county:
        stmt = stmt.join(County, ApplicationCluster.county_id == County.id).where(
            County.slug == county
        )
    if year:
        stmt = stmt.where(func.extract("year", ApplicationCluster.date_start) == year)
    if date_from:
        stmt = stmt.where(ApplicationCluster.date_end >= date_from)
    if date_to:
        stmt = stmt.where(ApplicationCluster.date_start <= date_to)
    if method:
        stmt = stmt.where(ApplicationCluster.method == method)
    if landowner:
        stmt = stmt.where(ApplicationCluster.owner_name.ilike(f"%{landowner}%"))
    if kind == DocumentKind.NOTICE_OF_INTENT:
        stmt = stmt.where(ApplicationCluster.is_planned.is_(True))
    elif kind == DocumentKind.USE_REPORT:
        stmt = stmt.where(ApplicationCluster.is_planned.is_(False))
    if flagged:
        stmt = stmt.where(ApplicationCluster.flags["highest_level"].as_string() == "red")

    if chemical or applicator or q:
        member = select(ClusterRecord.cluster_id).join(
            PurRecord, PurRecord.id == ClusterRecord.record_id
        )
        conditions = []
        if applicator:
            conditions.append(PurRecord.applicator_name.ilike(f"%{applicator}%"))
        if chemical:
            product_ids = select(ProductIngredient.product_id).join(
                ActiveIngredient, ActiveIngredient.id == ProductIngredient.ingredient_id
            ).where(ActiveIngredient.name.ilike(f"%{chemical}%"))
            chemical_records = select(PurProduct.record_id).where(
                or_(
                    PurProduct.product_name.ilike(f"%{chemical}%"),
                    PurProduct.product_id.in_(product_ids),
                )
            )
            conditions.append(PurRecord.id.in_(chemical_records))
        if q:
            text_records = select(PurProduct.record_id).where(
                PurProduct.product_name.ilike(f"%{q}%")
            )
            conditions.append(
                or_(
                    PurRecord.operator_name.ilike(f"%{q}%"),
                    PurRecord.location_text.ilike(f"%{q}%"),
                    PurRecord.applicator_name.ilike(f"%{q}%"),
                    PurRecord.site_id.ilike(f"%{q}%"),
                    PurRecord.mtrs.ilike(f"%{q}%"),
                    PurRecord.permit_number.ilike(f"%{q}%"),
                    PurRecord.id.in_(text_records),
                )
            )
        if conditions:
            stmt = stmt.where(ApplicationCluster.id.in_(member.where(and_(*conditions))))

    total = session.scalar(
        select(func.count()).select_from(stmt.subquery())
    ) or 0
    rows = session.scalars(
        stmt.order_by(ApplicationCluster.date_start.desc().nullslast())
        .offset((page - 1) * size)
        .limit(size)
    ).all()

    return {
        "total": total,
        "page": page,
        "page_size": size,
        "pages": (total + size - 1) // size if size else 0,
        "applications": [_cluster_summary(session, row) for row in rows],
    }


@router.get("/applications/{slug}")
def get_application(slug: str, session: Session = Depends(get_session),
                    _: Principal = Depends(rate_limit)):
    """One application, with everything its public page shows."""
    cluster = session.scalar(
        select(ApplicationCluster).where(ApplicationCluster.slug == slug, PUBLISHED)
    )
    if cluster is None:
        raise HTTPException(status_code=404, detail="Application not found")

    member_ids = [
        row.record_id
        for row in session.scalars(
            select(ClusterRecord).where(ClusterRecord.cluster_id == cluster.id)
        ).all()
    ]
    records = session.scalars(select(PurRecord).where(PurRecord.id.in_(member_ids))).all()

    parcels = []
    for link in session.scalars(
        select(ClusterParcel).where(ClusterParcel.cluster_id == cluster.id)
    ).all():
        parcel = session.get(Parcel, link.parcel_id)
        if parcel is None:
            continue
        parcels.append(
            {
                "apn": parcel.apn,
                "owner": parcel.owner_name,
                "acreage": parcel.acreage,
                "match_basis": link.match_basis,
                "confidence": link.confidence,
                "source": parcel.provider,
            }
        )

    summary = _cluster_summary(session, cluster)
    summary.update(
        {
            "mtrs": sorted({r.mtrs for r in records if r.mtrs}),
            "site_ids": sorted({r.site_id for r in records if r.site_id}),
            "permit_numbers": sorted({r.permit_number for r in records if r.permit_number}),
            "parcels": parcels,
            "flags": cluster.flags,
            "confidence": cluster.confidence,
            "records": [
                {
                    "document_number": r.document_number,
                    "kind": r.record_kind,
                    "site_id": r.site_id,
                    "mtrs": r.mtrs,
                    "date_start": r.date_start.isoformat() if r.date_start else None,
                    "date_end": r.date_end.isoformat() if r.date_end else None,
                    "method": r.method,
                    "operator": r.operator_name,
                    "applicator": r.applicator_name,
                    "applicator_license": r.applicator_license,
                    "treated_amount": r.treated_amount,
                    "treated_units": r.treated_units,
                    "commodity": r.commodity,
                    "products": [
                        {
                            "name": p.product_name,
                            "epa_reg_no": p.epa_reg_no,
                            "quantity": float(p.quantity) if p.quantity is not None else None,
                            "units": p.quantity_units,
                        }
                        for p in session.scalars(
                            select(PurProduct).where(PurProduct.record_id == r.id)
                        ).all()
                    ],
                }
                for r in records
            ],
        }
    )
    return summary


@router.get("/chemicals")
def list_chemicals(session: Session = Depends(get_session), _: Principal = Depends(rate_limit)):
    rows = session.scalars(select(ActiveIngredient).order_by(ActiveIngredient.name)).all()
    return {
        "chemicals": [
            {
                "name": row.name,
                "slug": row.slug,
                "pesticide_type": row.pesticide_type,
                "is_california_restricted": row.is_california_restricted,
                "is_watchlisted": row.is_watchlisted,
                "url": f"/chemical/{row.slug}/",
            }
            for row in rows
        ]
    }


@router.get("/chemicals/{slug}")
def get_chemical(slug: str, session: Session = Depends(get_session),
                 _: Principal = Depends(rate_limit)):
    ingredient = session.scalar(
        select(ActiveIngredient).where(ActiveIngredient.slug == slug)
    )
    if ingredient is None:
        raise HTTPException(status_code=404, detail="Chemical not found")

    product_ids = [
        link.product_id
        for link in session.scalars(
            select(ProductIngredient).where(ProductIngredient.ingredient_id == ingredient.id)
        ).all()
    ]
    products = (
        session.scalars(select(Product).where(Product.id.in_(product_ids))).all()
        if product_ids
        else []
    )
    record_ids = [
        row.record_id
        for row in session.scalars(
            select(PurProduct).where(PurProduct.product_id.in_(product_ids))
        ).all()
    ] if product_ids else []
    cluster_ids = {
        row.cluster_id
        for row in session.scalars(
            select(ClusterRecord).where(ClusterRecord.record_id.in_(record_ids))
        ).all()
    } if record_ids else set()
    clusters = (
        session.scalars(
            select(ApplicationCluster)
            .where(ApplicationCluster.id.in_(cluster_ids), PUBLISHED)
            .order_by(ApplicationCluster.date_start.desc())
        ).all()
        if cluster_ids
        else []
    )

    from app.models import ChemicalFlagRow

    flags = session.scalars(
        select(ChemicalFlagRow).where(ChemicalFlagRow.subject_name == ingredient.name)
    ).all()

    return {
        "name": ingredient.name,
        "slug": ingredient.slug,
        "cas_number": ingredient.cas_number,
        "chemical_class": ingredient.chemical_class,
        "pesticide_type": ingredient.pesticide_type,
        "sections": {
            "overview": ingredient.overview,
            "groundwater": ingredient.groundwater,
            "surface_water": ingredient.surface_water,
            "persistence": ingredient.persistence,
            "ecological": ingredient.ecological,
            "human_health": ingredient.human_health,
        },
        "flags": [
            {
                "level": f.level,
                "label": f.label,
                "detail": f.detail,
                "is_regulatory": f.is_regulatory,
                "source": f.source_name,
                "source_url": f.source_url,
            }
            for f in flags
        ],
        "products": [
            {"name": p.name, "epa_reg_no": p.base_epa_reg_no, "registrant": p.registrant}
            for p in products
        ],
        "applications": [_cluster_summary(session, c) for c in clusters],
    }


@router.get("/companies/{slug}")
def get_company(slug: str, session: Session = Depends(get_session),
                _: Principal = Depends(rate_limit)):
    company = session.scalar(
        select(Company).where(Company.slug == slug, Company.is_published.is_(True))
    )
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return {
        "name": company.name,
        "slug": company.slug,
        "business_type": company.business_type,
        "dpr_license": company.dpr_license,
        "license_status": company.license_status,
        "license_expiration": (
            company.license_expiration.isoformat() if company.license_expiration else None
        ),
        "business_address": company.business_address,
        "phone": company.phone,
        "email": company.email,
        "website": company.website,
        "aliases": [a.alias for a in company.aliases],
    }


@router.get("/people/{slug}")
def get_person(slug: str, session: Session = Depends(get_session),
               _: Principal = Depends(rate_limit)):
    person = session.scalar(
        select(Person).where(Person.slug == slug, Person.is_published.is_(True))
    )
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    employer = session.get(Company, person.employer_id) if person.employer_id else None
    return {
        "name": person.display_name,
        "slug": person.slug,
        "job_title": person.job_title,
        "employer": {"name": employer.name, "slug": employer.slug} if employer else None,
        "business_phone": person.business_phone,
        "business_email": person.business_email,
        "photo_url": person.photo_url,
        "licenses": [
            {
                "type": lic.license_type,
                "number": lic.number,
                "category": lic.category,
                "status": lic.status,
                "expires_on": lic.expires_on.isoformat() if lic.expires_on else None,
                "source": lic.source_url,
            }
            for lic in person.licenses
        ],
    }


@router.get("/stats", dependencies=[Depends(require_capability(Capability.READ_AGGREGATES))])
def statistics(
    session: Session = Depends(get_session),
    _: Principal = Depends(rate_limit),
    county: str | None = None,
    group_by: str = Query("county", pattern="^(county|year|applicator|landowner|method|product)$"),
):
    """Totals across published applications.

    Part of the subscriber tier: the aggregate view of the whole dataset is
    the commercially valuable product, so it requires a key.
    """
    from app.api.stats import compute_statistics

    return compute_statistics(session, county=county, group_by=group_by)
