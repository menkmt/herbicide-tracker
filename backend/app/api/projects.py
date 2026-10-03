"""THP and project maps: boundaries that locate applications, and the map
documents people want to see.

An administrator links a project (a THP number, say) to one or more
applications and uploads either or both of:

* a **boundary** (KML, KMZ, GeoJSON, zipped shapefile). It is drawn on the
  map, and each linked application is relocated to where its reported
  sections overlap the unit — far tighter than a square-mile section;
* a **map document** (PDF or image) shown on the applications' pages.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import MultiPolygon
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import rate_limit, require_admin
from app.core.access import Principal
from app.db import get_session
from app.maps.boundaries import (
    BOUNDARY_SUFFIXES,
    DOCUMENT_SUFFIXES,
    BoundaryError,
    read_boundary,
)
from app.models import (
    ApplicationCluster,
    ClusterRecord,
    County,
    PlssSection,
    Project,
    ProjectDocument,
    PurRecord,
)
from app.pipeline.storage import get_storage

router = APIRouter(prefix="/api", tags=["projects"])

MAX_BYTES = 50 * 1024 * 1024


def locate_in_project(session: Session, cluster: ApplicationCluster, boundary: MultiPolygon) -> str:
    """Give an application the part of the unit inside its reported sections,
    or the whole unit when they do not overlap. Parcel geometry wins."""
    basis = (cluster.scoring or {}).get("geom_basis")
    if cluster.geom is not None and basis not in (None, "plss_section", "project_boundary"):
        return "kept parcel outline"
    sections = session.scalar(
        select(func.ST_Union(PlssSection.geom))
        .select_from(ClusterRecord)
        .join(PurRecord, PurRecord.id == ClusterRecord.record_id)
        .join(PlssSection, PlssSection.id == PurRecord.plss_section_id)
        .where(ClusterRecord.cluster_id == cluster.id, PlssSection.geom.is_not(None))
    )
    unit = from_shape(boundary, srid=4326)
    geom, how = unit, "the whole project unit"
    if sections is not None:
        inside = func.ST_CollectionExtract(func.ST_Intersection(unit, sections), 3)
        overlap = session.scalar(select(func.ST_Multi(inside)))
        if overlap is not None and not session.scalar(select(func.ST_IsEmpty(overlap))):
            geom, how = overlap, "the part of the unit inside its reported sections"
    cluster.geom = geom
    scoring = dict(cluster.scoring or {})
    scoring["geom_basis"] = "project_boundary"
    cluster.scoring = scoring
    return how


@router.get("/admin/applications/options")
def application_options(session: Session = Depends(get_session),
                        _: Principal = Depends(require_admin)) -> dict:
    rows = session.execute(
        select(ApplicationCluster.slug, ApplicationCluster.title, ApplicationCluster.date_start,
               ApplicationCluster.status, County.name, Project.identifier)
        .outerjoin(County, County.id == ApplicationCluster.county_id)
        .outerjoin(Project, Project.id == ApplicationCluster.project_id)
        .order_by(ApplicationCluster.date_start.desc().nullslast())
    ).all()
    return {"applications": [
        {"slug": s, "title": t, "date": d.isoformat() if d else None, "status": st,
         "county": c, "project": p} for s, t, d, st, c, p in rows]}


@router.post("/admin/projects/map")
async def upload_project_map(
    identifier: str = Form(..., min_length=2, max_length=64),
    name: str | None = Form(None),
    kind: str = Form("THP"),
    applications: str = Form("", description="comma-separated application slugs"),
    source: str | None = Form(None),
    files: list[UploadFile] = File(default=[]),
    session: Session = Depends(get_session),
    _: Principal = Depends(require_admin),
) -> dict:
    identifier = identifier.strip().upper()
    project = session.scalar(select(Project).where(Project.identifier == identifier))
    if project is None:
        project = Project(identifier=identifier)
        session.add(project)
    if name:
        project.name = name.strip()
    project.kind = kind.strip() or "THP"
    session.flush()

    report: dict = {"project": identifier, "boundary": None, "documents": [], "linked": [],
                    "errors": []}
    boundary: MultiPolygon | None = None
    for upload in files:
        content = await upload.read()
        filename = Path(upload.filename or "file").name
        suffix = Path(filename).suffix.lower()
        if not content:
            continue
        if len(content) > MAX_BYTES:
            report["errors"].append(f"{filename}: larger than 50 MB")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / filename
            path.write_bytes(content)
            if suffix in BOUNDARY_SUFFIXES:
                try:
                    shape_ = read_boundary(path)
                except BoundaryError as exc:
                    report["errors"].append(str(exc))
                    continue
                except Exception as exc:  # noqa: BLE001 - a malformed file, reported
                    report["errors"].append(f"{filename}: could not be read ({exc})")
                    continue
                boundary = shape_ if boundary is None else MultiPolygon(
                    list(boundary.geoms) + list(shape_.geoms))
                report["boundary"] = filename
            elif suffix in DOCUMENT_SUFFIXES:
                digest = hashlib.sha256(content).hexdigest()
                key = get_storage().put(path, sha256=digest, filename=filename)
                doc = ProjectDocument(
                    project_id=project.id, title=f"{project.kind} {identifier} map",
                    filename=filename, storage_key=key, byte_size=len(content),
                    content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
                    source_note=source,
                )
                session.add(doc)
                report["documents"].append(filename)
            else:
                report["errors"].append(
                    f"{filename}: use KML, KMZ, GeoJSON or a zipped shapefile for a boundary, "
                    "or PDF / image for a map document")
    if boundary is not None:
        # A new boundary replaces the old one, so a corrected map is not
        # drawn on top of the one it fixes.
        project.geom = from_shape(boundary, srid=4326)

    slugs = [s.strip() for s in applications.split(",") if s.strip()]
    for slug in slugs:
        cluster = session.scalar(select(ApplicationCluster).where(ApplicationCluster.slug == slug))
        if cluster is None:
            report["errors"].append(f"no application {slug}")
            continue
        cluster.project_id = project.id
        located = None
        if project.geom is not None:
            located = locate_in_project(session, cluster, to_shape(project.geom))
        report["linked"].append({"slug": slug, "located_by": located})
    session.commit()
    return report


def _safe_name(filename: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]", "_", filename)[:120] or "map"


@router.get("/project-documents/{doc_id}")
def project_document(doc_id: int, session: Session = Depends(get_session),
                     _: Principal = Depends(rate_limit)) -> Response:
    doc = session.get(ProjectDocument, doc_id)
    if doc is None:
        raise HTTPException(404, "No such document")
    return Response(
        content=get_storage().open(doc.storage_key), media_type=doc.content_type,
        headers={"Content-Disposition": f'inline; filename="{_safe_name(doc.filename)}"',
                 "Cache-Control": "public, max-age=86400"},
    )


def project_for(session: Session, cluster: ApplicationCluster) -> dict | None:
    """The project block on an application page."""
    if not cluster.project_id:
        return None
    project = session.get(Project, cluster.project_id)
    if project is None:
        return None
    docs = session.scalars(
        select(ProjectDocument).where(ProjectDocument.project_id == project.id)
        .order_by(ProjectDocument.id)
    ).all()
    return {
        "identifier": project.identifier,
        "name": project.name,
        "kind": project.kind,
        "has_boundary": project.geom is not None,
        "documents": [
            {"id": d.id, "title": d.title, "filename": d.filename,
             "content_type": d.content_type, "url": f"/api/project-documents/{d.id}",
             "source": d.source_note}
            for d in docs
        ],
    }
