"""Geographic endpoints: the interactive map feed and address/radius search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from geoalchemy2 import Geography
from geoalchemy2 import functions as geo
from sqlalchemy import cast, func, select
from sqlalchemy.orm import Session

from app.api.deps import rate_limit
from app.config import Settings, get_settings
from app.core.access import Principal
from app.db import get_session
from app.models import (
    ApplicationCluster,
    ClusterParcel,
    ClusterRecord,
    County,
    Parcel,
    PlssSection,
    PurRecord,
)
from app.providers.geocode import GeocodeError, get_geocoder

router = APIRouter(prefix="/api", tags=["geo"])

#: Metres per mile, for buffering a search point.
METRES_PER_MILE = 1609.344

#: Largest radius a single search may request.
MAX_RADIUS_MILES = 50.0


@router.get("/map/applications")
def map_applications(
    session: Session = Depends(get_session),
    _: Principal = Depends(rate_limit),
    county: str | None = None,
    year: int | None = None,
    slug: str | None = Query(None, description="A single application's parcels"),
    bbox: str | None = Query(None, description="minLon,minLat,maxLon,maxLat"),
    limit: int = Query(2000, ge=1, le=5000),
):
    """GeoJSON of published applications, for the interactive map.

    Parcel polygons where the property has been identified. Where it has not,
    the reported PLSS section instead, marked ``geometry_kind: section`` so the
    map draws it differently and the popup says it is the square mile the
    county was told about, not the area sprayed. Each feature carries enough
    for a click popup plus a link to the full page.
    """
    stmt = (
        select(
            ApplicationCluster.id,
            ApplicationCluster.slug,
            ApplicationCluster.title,
            ApplicationCluster.date_start,
            ApplicationCluster.total_acres,
            ApplicationCluster.method,
            ApplicationCluster.owner_name,
            ApplicationCluster.flags,
            Parcel.apn,
            geo.ST_AsGeoJSON(Parcel.geom),
        )
        .join(ClusterParcel, ClusterParcel.cluster_id == ApplicationCluster.id)
        .join(Parcel, Parcel.id == ClusterParcel.parcel_id)
        .where(ApplicationCluster.status == "published", Parcel.geom.isnot(None))
    )
    if slug:
        stmt = stmt.where(ApplicationCluster.slug == slug)
    if county:
        stmt = stmt.join(County, ApplicationCluster.county_id == County.id).where(
            County.slug == county
        )
    if year:
        stmt = stmt.where(func.extract("year", ApplicationCluster.date_start) == year)
    if bbox:
        try:
            min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox.split(","))
        except ValueError as exc:
            raise HTTPException(400, "bbox must be minLon,minLat,maxLon,maxLat") from exc
        envelope = geo.ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat, 4326)
        stmt = stmt.where(geo.ST_Intersects(Parcel.geom, envelope))

    import json

    def feature(cid, slug, title, start, acres, method, owner, flags, geometry, **extra):
        flags = flags or {}
        return {
            "type": "Feature",
            "geometry": json.loads(geometry),
            "properties": {
                "cluster_id": cid,
                "slug": slug,
                "title": title,
                "owner": owner,
                "date": start.isoformat() if start else None,
                "acres": acres,
                "method": method,
                "flag_level": flags.get("highest_level"),
                "flag_headline": flags.get("headline"),
                "url": f"/application/{slug}",
                **extra,
            },
        }

    features = []
    with_parcels: set[int] = set()
    for row in session.execute(stmt.limit(limit)).all():
        (cid, slug_, title, start, acres, method, owner, flags, apn, geometry) = row
        if not geometry:
            continue
        with_parcels.add(cid)
        features.append(feature(cid, slug_, title, start, acres, method, owner, flags, geometry,
                                apn=apn, geometry_kind="parcel"))

    # Applications whose parcels are not matched yet: their reported sections.
    sections = (
        select(
            ApplicationCluster.id,
            ApplicationCluster.slug,
            ApplicationCluster.title,
            ApplicationCluster.date_start,
            ApplicationCluster.total_acres,
            ApplicationCluster.method,
            ApplicationCluster.owner_name,
            ApplicationCluster.flags,
            PlssSection.mtrs,
            geo.ST_AsGeoJSON(PlssSection.geom),
        )
        .join(ClusterRecord, ClusterRecord.cluster_id == ApplicationCluster.id)
        .join(PurRecord, PurRecord.id == ClusterRecord.record_id)
        .join(PlssSection, PlssSection.id == PurRecord.plss_section_id)
        .where(ApplicationCluster.status == "published", PlssSection.geom.isnot(None))
    )
    if slug:
        sections = sections.where(ApplicationCluster.slug == slug)
    if county:
        sections = sections.join(County, ApplicationCluster.county_id == County.id).where(
            County.slug == county
        )
    if year:
        sections = sections.where(func.extract("year", ApplicationCluster.date_start) == year)
    if bbox:
        sections = sections.where(geo.ST_Intersects(PlssSection.geom, envelope))
    # Several records in one application usually share a section; draw it once.
    # (Deduplicated here rather than with DISTINCT, which Postgres cannot apply
    # to the JSON flags column.)
    drawn: set[tuple[int, str]] = set()
    for row in session.execute(sections.limit(limit * 4)).all():
        (cid, slug_, title, start, acres, method, owner, flags, mtrs, geometry) = row
        if cid in with_parcels or not geometry or (cid, mtrs) in drawn:
            continue
        drawn.add((cid, mtrs))
        features.append(feature(cid, slug_, title, start, acres, method, owner, flags, geometry,
                                mtrs=mtrs, geometry_kind="section"))

    return {"type": "FeatureCollection", "features": features}


@router.get("/search/radius")
def radius_search(
    session: Session = Depends(get_session),
    _: Principal = Depends(rate_limit),
    settings: Settings = Depends(get_settings),
    address: str | None = Query(None, description="Street address or place name"),
    lat: float | None = None,
    lon: float | None = None,
    miles: float = Query(1.0, gt=0, le=MAX_RADIUS_MILES),
    limit: int = Query(200, ge=1, le=1000),
):
    """Find applications within a radius of an address.

    Distance is measured to the **nearest edge** of an application's geometry,
    not to its centroid: a person living beside a 600-acre parcel is next to
    the application even though its centre is a mile away.

    The submitted address is geocoded and used for this request only. It is
    not stored, and the response deliberately echoes back only the resolved
    coordinate.
    """
    if lat is None or lon is None:
        if not address:
            raise HTTPException(400, "Provide either an address or lat/lon")
        try:
            located = get_geocoder(settings).geocode(address)
        except GeocodeError as exc:
            raise HTTPException(502, f"Address lookup failed: {exc}") from exc
        if located is None:
            raise HTTPException(404, "That address could not be located")
        lat, lon = located.latitude, located.longitude
        resolved_label = located.display_name
    else:
        resolved_label = None

    point = geo.ST_SetSRID(geo.ST_MakePoint(lon, lat), 4326)
    # Measured on the geography type so the distance is in real metres
    # regardless of latitude, rather than in degrees.
    distance = geo.ST_Distance(
        cast(ApplicationCluster.geom, Geography),
        cast(point, Geography),
    )
    radius_metres = miles * METRES_PER_MILE

    stmt = (
        select(
            ApplicationCluster.slug,
            ApplicationCluster.title,
            ApplicationCluster.owner_name,
            ApplicationCluster.date_start,
            ApplicationCluster.total_acres,
            ApplicationCluster.method,
            ApplicationCluster.flags,
            distance.label("metres"),
        )
        .where(
            ApplicationCluster.status == "published",
            ApplicationCluster.geom.isnot(None),
            distance <= radius_metres,
        )
        .order_by(distance)
        .limit(limit)
    )

    results = []
    for slug, title, owner, start, acres, method, flags, metres in session.execute(stmt).all():
        flags = flags or {}
        results.append(
            {
                "slug": slug,
                "title": title,
                "owner": owner,
                "date": start.isoformat() if start else None,
                "acres": acres,
                "method": method,
                "distance_miles": round(float(metres) / METRES_PER_MILE, 3),
                "flag_level": flags.get("highest_level"),
                "flag_headline": flags.get("headline"),
                "url": f"/application/{slug}",
            }
        )

    return {
        "centre": {"lat": lat, "lon": lon, "resolved": resolved_label},
        "radius_miles": miles,
        "count": len(results),
        "results": results,
        "distance_basis": (
            "nearest edge of the application's parcels, or of its reported section "
            "where the parcels are not yet identified"
        ),
        "privacy_note": "The searched address is not stored.",
    }
