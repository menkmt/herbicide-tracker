"""Administrator API: import, review and publish.

Every endpoint requires administrator authentication, and every change writes
an audit-log entry recording who changed what, when, and what it was before.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.access import Principal
from app.db import get_session
from app.models import (
    ApplicationCluster,
    AuditLog,
    ClusterRecord,
    ImportBatch,
    PurRecord,
    ReviewItem,
)
from app.pipeline.ingest import ingest_files

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _audit(
    session: Session,
    principal: Principal,
    *,
    action: str,
    entity_type: str,
    entity_id: int | None,
    field_name: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    note: str | None = None,
) -> None:
    session.add(
        AuditLog(
            actor=principal.subject or "admin",
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            field_name=field_name,
            old_value={"value": old_value} if old_value is not None else None,
            new_value={"value": new_value} if new_value is not None else None,
            note=note,
        )
    )


@router.post("/import")
async def import_files(
    files: list[UploadFile] = File(...),
    county: str | None = Form(None),
    label: str | None = Form(None),
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_admin),
):
    """Drag-and-drop import. Returns the summary the dashboard renders."""
    if not files:
        raise HTTPException(400, "No files were uploaded")

    with tempfile.TemporaryDirectory() as tmpdir:
        paths: list[Path] = []
        for upload in files:
            # Keep the original filename: it is part of the citation.
            safe_name = Path(upload.filename or "upload").name
            destination = Path(tmpdir) / safe_name
            with destination.open("wb") as handle:
                shutil.copyfileobj(upload.file, handle)
            paths.append(destination)

        summary = ingest_files(session, paths, county=county, label=label)

    _audit(
        session,
        principal,
        action="import",
        entity_type="import_batch",
        entity_id=summary.batch_id,
        note=f"{summary.files_processed} file(s)",
    )
    session.commit()
    return {"summary": summary.to_dict(), "text": summary.render()}


@router.get("/batches")
def list_batches(session: Session = Depends(get_session), _: Principal = Depends(require_admin)):
    rows = session.scalars(
        select(ImportBatch).order_by(ImportBatch.created_at.desc()).limit(50)
    ).all()
    return {
        "batches": [
            {
                "id": b.id,
                "label": b.label,
                "origin": b.origin,
                "state": b.state,
                "created_at": b.created_at.isoformat(),
                "records_extracted": b.records_extracted,
                "clusters_created": b.clusters_created,
                "ready": b.ready_count,
                "review": b.review_count,
                "failed": b.files_failed,
            }
            for b in rows
        ]
    }


@router.get("/queue")
def review_queue(
    state: str = "open",
    reason: str | None = None,
    session: Session = Depends(get_session),
    _: Principal = Depends(require_admin),
):
    """The review queue, grouped by the reason a human is needed."""
    stmt = select(ReviewItem).where(ReviewItem.state == state)
    if reason:
        stmt = stmt.where(ReviewItem.reason == reason)
    items = session.scalars(stmt.order_by(ReviewItem.created_at.desc()).limit(500)).all()

    counts = dict(
        session.execute(
            select(ReviewItem.reason, func.count(ReviewItem.id))
            .where(ReviewItem.state == state)
            .group_by(ReviewItem.reason)
        ).all()
    )
    status_counts = dict(
        session.execute(
            select(ApplicationCluster.status, func.count(ApplicationCluster.id)).group_by(
                ApplicationCluster.status
            )
        ).all()
    )

    return {
        "counts_by_reason": counts,
        "counts_by_status": status_counts,
        "items": [
            {
                "id": item.id,
                "entity_type": item.entity_type,
                "entity_id": item.entity_id,
                "reason": item.reason,
                "detail": item.detail,
                "options": item.options,
                "created_at": item.created_at.isoformat(),
            }
            for item in items
        ],
    }


class PublishRequest(BaseModel):
    note: str | None = None


@router.post("/applications/{cluster_id}/publish")
def publish(
    cluster_id: int,
    body: PublishRequest | None = None,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_admin),
):
    cluster = session.get(ApplicationCluster, cluster_id)
    if cluster is None:
        raise HTTPException(404, "Application not found")
    previous = cluster.status
    cluster.status = "published"
    cluster.published_at = datetime.now(UTC)
    _audit(
        session,
        principal,
        action="publish",
        entity_type="application_cluster",
        entity_id=cluster.id,
        field_name="status",
        old_value=previous,
        new_value="published",
        note=(body.note if body else None),
    )
    session.commit()
    return {"slug": cluster.slug, "status": cluster.status}


@router.post("/applications/publish-ready")
def publish_all_ready(
    session: Session = Depends(get_session), principal: Principal = Depends(require_admin)
):
    """Publish everything the pipeline marked ready. The one-click path."""
    rows = session.scalars(
        select(ApplicationCluster).where(ApplicationCluster.status == "ready")
    ).all()
    for cluster in rows:
        cluster.status = "published"
        cluster.published_at = datetime.now(UTC)
        _audit(
            session,
            principal,
            action="publish",
            entity_type="application_cluster",
            entity_id=cluster.id,
            field_name="status",
            old_value="ready",
            new_value="published",
        )
    session.commit()
    return {"published": len(rows)}


class TitleRequest(BaseModel):
    title: str


@router.post("/applications/{cluster_id}/title")
def set_title(
    cluster_id: int,
    body: TitleRequest,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_admin),
):
    """Override an application's title.

    An administrator's title always wins over a derived one, and the override
    is stored separately so the derived title is never lost.
    """
    cluster = session.get(ApplicationCluster, cluster_id)
    if cluster is None:
        raise HTTPException(404, "Application not found")
    previous = cluster.title
    cluster.title_override = body.title
    cluster.title = body.title
    cluster.title_basis = "set by a Protect Lassen administrator"
    _audit(
        session,
        principal,
        action="retitle",
        entity_type="application_cluster",
        entity_id=cluster.id,
        field_name="title",
        old_value=previous,
        new_value=body.title,
    )
    session.commit()
    return {"slug": cluster.slug, "title": cluster.title}


class MergeRequest(BaseModel):
    source_cluster_id: int


@router.post("/applications/{cluster_id}/merge")
def merge_clusters(
    cluster_id: int,
    body: MergeRequest,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_admin),
):
    """Move every record from one application into another.

    The emptied cluster is marked rejected rather than deleted, so the merge
    is auditable and reversible.
    """
    target = session.get(ApplicationCluster, cluster_id)
    source = session.get(ApplicationCluster, body.source_cluster_id)
    if target is None or source is None:
        raise HTTPException(404, "Application not found")
    if target.id == source.id:
        raise HTTPException(400, "Cannot merge an application into itself")

    moved = 0
    for member in session.scalars(
        select(ClusterRecord).where(ClusterRecord.cluster_id == source.id)
    ).all():
        member.cluster_id = target.id
        member.assigned_by = "manual"
        moved += 1

    source.status = "rejected"
    _recompute(session, target)
    _audit(
        session,
        principal,
        action="merge",
        entity_type="application_cluster",
        entity_id=target.id,
        note=f"merged {moved} record(s) from cluster {source.id}",
    )
    session.commit()
    return {"target": target.slug, "records_moved": moved}


class SplitRequest(BaseModel):
    record_ids: list[int]
    title: str | None = None


@router.post("/applications/{cluster_id}/split")
def split_cluster(
    cluster_id: int,
    body: SplitRequest,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_admin),
):
    """Move records out of an application into a new one."""
    cluster = session.get(ApplicationCluster, cluster_id)
    if cluster is None:
        raise HTTPException(404, "Application not found")
    if not body.record_ids:
        raise HTTPException(400, "No records were selected")

    from slugify import slugify

    from app.pipeline.ingest import _unique_slug

    title = body.title or f"{cluster.title} (split)"
    new_cluster = ApplicationCluster(
        slug=_unique_slug(session, slugify(title)),
        county_id=cluster.county_id,
        title=title,
        title_basis="split from another application by an administrator",
        title_override=body.title,
        owner_name=cluster.owner_name,
        method=cluster.method,
        status="needs_review",
        confidence="manual",
    )
    session.add(new_cluster)
    session.flush()

    moved = 0
    for member in session.scalars(
        select(ClusterRecord).where(
            ClusterRecord.cluster_id == cluster.id,
            ClusterRecord.record_id.in_(body.record_ids),
        )
    ).all():
        member.cluster_id = new_cluster.id
        member.assigned_by = "manual"
        moved += 1

    _recompute(session, cluster)
    _recompute(session, new_cluster)
    _audit(
        session,
        principal,
        action="split",
        entity_type="application_cluster",
        entity_id=cluster.id,
        note=f"moved {moved} record(s) to cluster {new_cluster.id}",
    )
    session.commit()
    return {"new_slug": new_cluster.slug, "records_moved": moved}


class ResolveRequest(BaseModel):
    decision: str
    note: str | None = None


@router.post("/queue/{item_id}/resolve")
def resolve_item(
    item_id: int,
    body: ResolveRequest,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_admin),
):
    item = session.get(ReviewItem, item_id)
    if item is None:
        raise HTTPException(404, "Review item not found")
    item.state = "resolved"
    item.resolved_by = principal.subject or "admin"
    item.resolved_at = datetime.now(UTC)
    item.resolution = {"decision": body.decision, "note": body.note}
    _audit(
        session,
        principal,
        action="resolve_review",
        entity_type=item.entity_type,
        entity_id=item.entity_id,
        note=f"{item.reason}: {body.decision}",
    )
    session.commit()
    return {"id": item.id, "state": item.state}


def _recompute(session: Session, cluster: ApplicationCluster) -> None:
    """Recalculate a cluster's derived figures after records move."""
    member_ids = [
        row.record_id
        for row in session.scalars(
            select(ClusterRecord).where(ClusterRecord.cluster_id == cluster.id)
        ).all()
    ]
    if not member_ids:
        cluster.total_acres = None
        cluster.date_start = cluster.date_end = None
        return
    records = session.scalars(select(PurRecord).where(PurRecord.id.in_(member_ids))).all()
    starts = [r.date_start for r in records if r.date_start]
    ends = [r.date_end or r.date_start for r in records if r.date_end or r.date_start]
    acres = [r.treated_amount for r in records if r.treated_amount is not None]
    cluster.date_start = min(starts) if starts else None
    cluster.date_end = max(ends) if ends else None
    cluster.total_acres = round(sum(acres), 2) if acres else None
    cluster.acreage_is_partial = len(acres) != len(records)
    methods = {r.method for r in records}
    cluster.is_mixed_method = len(methods - {"unknown"}) > 1
    cluster.method = "aerial" if "aerial" in methods else next(iter(methods), "unknown")
