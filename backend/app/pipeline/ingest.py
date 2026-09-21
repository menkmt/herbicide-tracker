"""The import pipeline: files in, reviewable applications out.

This is the "drop PURs in, the app does the rest" path.  For each uploaded
file it runs the stages the build plan lays out — parse, normalise, cluster,
resolve, validate — and finishes with a summary telling the administrator how
much is ready to publish and what needs a decision.

Design commitments:

* **Idempotent.**  Files are keyed by SHA-256, so re-uploading the same
  document reuses the stored object and does not create duplicate records.
* **Non-destructive.**  Extraction writes source records; everything else
  writes rows that *reference* them.
* **Explaining, not guessing.**  Any uncertainty becomes a review item with a
  reason a person can act on, rather than a silently-dropped record or an
  invented value.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from slugify import slugify
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clustering.cluster import cluster_records
from app.clustering.score import DEFAULT_WEIGHTS, ClusterWeights
from app.core.confidence import Confidence, is_publishable
from app.core.coverage import DocumentKind
from app.core.normalize import company_key
from app.core.provenance import Provenance
from app.core.site_category import classify_site
from app.core.units import normalize_quantity
from app.extraction.base import ExtractionResult, PermitRecord, PurRecord
from app.extraction.registry import extract_file
from app.extraction.text_source import sha256_file
from app.models import (
    ApplicationCluster as ClusterRow,
)
from app.models import (
    ClusterRecord,
    County,
    FactSource,
    ImportBatch,
    Permit,
    PermitContact,
    PermitMaterial,
    PermitSite,
    PurProduct,
    ReviewItem,
    SourceFile,
)
from app.models import (
    PurRecord as PurRecordRow,
)
from app.pipeline import chemicals_stage
from app.pipeline.storage import SourceStorage, get_storage

logger = logging.getLogger(__name__)


@dataclass
class FileOutcome:
    """What happened to one uploaded file."""

    filename: str
    sha256: str
    profile: str | None = None
    status: str = "processed"  # processed | duplicate | failed
    records: int = 0
    permits: int = 0
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "sha256": self.sha256,
            "profile": self.profile,
            "status": self.status,
            "records": self.records,
            "permits": self.permits,
            "notes": self.notes,
            "error": self.error,
        }


@dataclass
class ImportSummary:
    """The screen the administrator sees after pressing Import."""

    batch_id: int | None = None
    files_processed: int = 0
    files_duplicate: int = 0
    files_failed: int = 0
    records_extracted: int = 0
    permits_extracted: int = 0
    permit_sites: int = 0
    clusters_created: int = 0
    ready_to_publish: int = 0
    needs_review: int = 0
    out_of_coverage: int = 0
    excluded_not_forestry: int = 0
    chemical_alerts: int = 0
    review_reasons: dict[str, int] = field(default_factory=dict)
    files: list[FileOutcome] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "files_processed": self.files_processed,
            "files_duplicate": self.files_duplicate,
            "files_failed": self.files_failed,
            "records_extracted": self.records_extracted,
            "permits_extracted": self.permits_extracted,
            "permit_sites": self.permit_sites,
            "clusters_created": self.clusters_created,
            "ready_to_publish": self.ready_to_publish,
            "needs_review": self.needs_review,
            "out_of_coverage": self.out_of_coverage,
            "excluded_not_forestry": self.excluded_not_forestry,
            "chemical_alerts": self.chemical_alerts,
            "review_reasons": self.review_reasons,
            "files": [f.to_dict() for f in self.files],
        }

    def render(self) -> str:
        """The plain-text summary from the build plan."""
        lines = [
            f"{self.files_processed} files processed",
            "",
            f"{self.records_extracted} PUR records extracted",
            f"{self.permits_extracted} permits ({self.permit_sites} permitted sites)",
            f"{self.clusters_created} application clusters created",
            f"{self.chemical_alerts} restricted/watchlist chemical alerts",
            "",
            f"{self.ready_to_publish} READY TO PUBLISH",
            "",
            f"{self.needs_review} NEED REVIEW",
        ]
        for reason, count in sorted(self.review_reasons.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {reason.replace('_', ' ')} ({count})")
        if self.files_duplicate:
            lines.extend(["", f"{self.files_duplicate} file(s) already imported (skipped)"])
        if self.files_failed:
            lines.extend(["", f"{self.files_failed} FAILED IMPORT"])
            for outcome in self.files:
                if outcome.status == "failed":
                    lines.append(f"- {outcome.filename}: {outcome.error}")
        if self.out_of_coverage:
            lines.extend(
                ["", f"{self.out_of_coverage} record(s) predate the tracker's 2020 coverage start"]
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def get_or_create_county(session: Session, name: str | None) -> County | None:
    if not name:
        return None
    cleaned = name.strip().removesuffix(" County").strip()
    slug = slugify(cleaned)
    county = session.scalar(select(County).where(County.slug == slug))
    if county is None:
        county = County(name=cleaned, slug=slug)
        session.add(county)
        session.flush()
    return county


def _record_provenance(
    session: Session,
    *,
    entity_type: str,
    entity_id: int,
    sources: dict[str, Provenance],
    source_file_id: int | None,
) -> None:
    """Persist per-field provenance for a saved row."""
    for field_name, provenance in sources.items():
        session.add(
            FactSource(
                entity_type=entity_type,
                entity_id=entity_id,
                field_name=field_name,
                source_type=provenance.source_type,
                source_name=provenance.source_name,
                source_id=provenance.source_id,
                source_url=provenance.source_url,
                source_file_id=source_file_id,
                locator=provenance.locator,
                extraction_method=provenance.extraction_method,
                confidence=provenance.confidence,
                retrieved_at=provenance.retrieved_at,
                notes={"notes": list(provenance.notes)} if provenance.notes else None,
            )
        )


def persist_record(
    session: Session,
    record: PurRecord,
    *,
    source_file: SourceFile,
    county: County | None,
) -> PurRecordRow:
    """Write one extracted PUR record, exactly as the document reported it."""
    classification = classify_site(record.commodity_code, record.commodity)
    start, end = record.date_range

    row = PurRecordRow(
        source_file_id=source_file.id,
        county_id=county.id if county else None,
        record_kind=record.record_kind,
        document_number=record.document_number,
        permit_number=record.permit_number,
        site_district=record.site_district,
        operator_name=record.operator_name,
        operator_key=company_key(record.operator_name) or None,
        applicator_name=record.applicator_name,
        applicator_key=company_key(record.applicator_name) or None,
        applicator_license=record.applicator_license,
        applicator_license_type=record.applicator_license_type,
        applicator_address=record.applicator_address,
        location_text=record.location_text,
        site_id=record.site_id,
        mtrs=record.mtrs,
        site_decode_method=record.site.method if record.site else None,
        site_confidence=str(record.site.confidence) if record.site else None,
        date_start=start,
        date_end=end,
        start_datetime=record.start_datetime,
        end_datetime=record.end_datetime,
        method=record.method,
        method_raw=record.method_raw,
        commodity=record.commodity,
        commodity_code=record.commodity_code,
        site_category=classification.category,
        site_category_confidence=str(classification.confidence),
        planted_amount=record.planted_amount,
        planted_units=record.planted_units,
        treated_amount=record.treated_amount,
        treated_units=record.treated_units,
        submittal_status=record.submittal_status,
        school_notification=record.school_notification,
        in_coverage=record.in_coverage,
        needs_review=record.needs_review,
        issues=[i.to_dict() for i in record.issues],
        raw=record.raw,
    )
    session.add(row)
    session.flush()

    for product in record.products:
        quantity = normalize_quantity(
            product.quantity, product.quantity_units, product_name=product.product_name
        )
        session.add(
            PurProduct(
                record_id=row.id,
                product_name=product.product_name,
                epa_reg_no=product.epa_reg_no,
                base_epa_reg_no=product.base_epa_reg_no,
                distributor_suffix=product.distributor_suffix,
                quantity=product.quantity,
                quantity_units=product.quantity_units,
                treated_amount=product.treated_amount,
                treated_units=product.treated_units,
                registration_expired=product.registration_expired,
                gallons=quantity.gallons,
                pounds=quantity.pounds,
            )
        )

    _record_provenance(
        session,
        entity_type="pur_record",
        entity_id=row.id,
        sources=record.field_sources,
        source_file_id=source_file.id,
    )
    return row


def persist_permit(
    session: Session,
    permit: PermitRecord,
    *,
    source_file: SourceFile,
    county: County | None,
) -> Permit:
    """Write a restricted-materials permit and its three tables."""
    existing = (
        session.scalar(select(Permit).where(Permit.permit_number == permit.permit_number))
        if permit.permit_number
        else None
    )
    if existing is not None:
        # A permit re-delivered in a later production is the same permit.
        return existing

    row = Permit(
        permit_number=permit.permit_number or f"unknown-{source_file.sha256[:8]}",
        county_id=county.id if county else None,
        operator_name=permit.operator_name,
        operator_key=company_key(permit.operator_name) or None,
        operator_id=permit.operator_id,
        agent_name=permit.agent_name,
        applicant_name=permit.applicant_name,
        applicant_title=permit.applicant_title,
        issued_on=permit.issued_on,
        valid_from=permit.valid_from,
        expires_on=permit.expires_on,
        permit_duration=permit.permit_duration,
        type_of_use=permit.type_of_use,
        source_file_id=source_file.id,
    )
    session.add(row)
    session.flush()

    for site in permit.sites:
        session.add(
            PermitSite(
                permit_id=row.id,
                site_id=site.site_id,
                mtrs=site.site.mtrs if site.site else None,
                mtrs_text=site.mtrs_text,
                site_name=site.site_name,
                district=site.district,
                commodity=site.commodity,
                commodity_code=site.commodity_code,
                permitted_acreage=site.acreage,
                permitted_materials=[list(m) for m in site.permitted_materials],
                has_conflict=bool(site.issues),
            )
        )
    for contact in permit.contacts:
        session.add(
            PermitContact(
                permit_id=row.id,
                name=contact.name,
                phone=contact.phone,
                license_number=contact.license_number,
                license_expiration=contact.license_expiration,
                contact_type=contact.contact_type,
                is_business=contact.is_business,
            )
        )
    for material in permit.permitted_materials:
        session.add(
            PermitMaterial(
                permit_id=row.id,
                number=material.get("number"),
                name=material.get("name"),
                pests=material.get("pests"),
                form=material.get("form"),
                methods=material.get("methods"),
                applicators=material.get("applicators"),
            )
        )

    _record_provenance(
        session,
        entity_type="permit",
        entity_id=row.id,
        sources=permit.field_sources,
        source_file_id=source_file.id,
    )
    return row


def _add_review(
    session: Session,
    *,
    entity_type: str,
    entity_id: int,
    reason: str,
    detail: str,
    batch_id: int | None,
    options: list | None = None,
) -> None:
    session.add(
        ReviewItem(
            entity_type=entity_type,
            entity_id=entity_id,
            reason=reason,
            detail=detail,
            options=options,
            batch_id=batch_id,
        )
    )


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------

def ingest_files(
    session: Session,
    paths: list[Path | str],
    *,
    county: str | None = None,
    label: str | None = None,
    origin: str = "upload",
    storage: SourceStorage | None = None,
    weights: ClusterWeights = DEFAULT_WEIGHTS,
    cpra_metadata: dict[str, dict[str, Any]] | None = None,
) -> ImportSummary:
    """Run the full import for a batch of files."""
    storage = storage or get_storage()
    summary = ImportSummary()

    batch = ImportBatch(label=label, origin=origin, files_total=len(paths))
    session.add(batch)
    session.flush()
    summary.batch_id = batch.id

    new_records: list[tuple[PurRecord, PurRecordRow]] = []

    for raw_path in paths:
        path = Path(raw_path)
        outcome = FileOutcome(filename=path.name, sha256="")
        try:
            digest = sha256_file(path)
            outcome.sha256 = digest

            existing = session.scalar(select(SourceFile).where(SourceFile.sha256 == digest))
            if existing is not None:
                outcome.status = "duplicate"
                outcome.notes.append(
                    f"already imported on {existing.created_at:%Y-%m-%d} as {existing.filename}"
                )
                summary.files_duplicate += 1
                summary.files.append(outcome)
                continue

            result: ExtractionResult = extract_file(path, county=county, sha256=digest)
            outcome.profile = result.profile
            outcome.notes.extend(result.notes)

            if not result.records and not result.permits:
                outcome.status = "failed"
                outcome.error = (
                    result.issues[0].detail if result.issues else "nothing could be extracted"
                )
                summary.files_failed += 1
                summary.files.append(outcome)
                continue

            storage_key = storage.put(path, sha256=digest, filename=path.name)
            metadata = (cpra_metadata or {}).get(path.name, {})
            source_file = SourceFile(
                filename=path.name,
                sha256=digest,
                byte_size=path.stat().st_size,
                storage_key=storage_key,
                profile=result.profile,
                document_kind=(
                    DocumentKind.PERMIT if result.permits else DocumentKind.USE_REPORT
                ),
                origin=origin,
                processing_state="processed",
                processing_notes={"notes": result.notes},
                used_ocr=any("OCR'd" in note for note in result.notes),
                cpra_agency=metadata.get("agency"),
                cpra_request_number=metadata.get("request_number"),
                cpra_production=metadata.get("production"),
                inquisitor_source_id=metadata.get("source_id"),
                received_at=datetime.now(UTC),
            )
            county_row = get_or_create_county(session, county or result.records[0].county_name
                                              if result.records else county)
            source_file.county_id = county_row.id if county_row else None
            session.add(source_file)
            session.flush()

            for permit in result.permits:
                permit_county = get_or_create_county(session, permit.county_name or county)
                permit_row = persist_permit(
                    session, permit, source_file=source_file, county=permit_county
                )
                summary.permits_extracted += 1
                summary.permit_sites += len(permit.sites)
                outcome.permits += 1
                for issue in permit.issues:
                    if issue.severity == "review":
                        _add_review(
                            session,
                            entity_type="permit",
                            entity_id=permit_row.id,
                            reason=issue.code,
                            detail=issue.detail,
                            batch_id=batch.id,
                        )
                        summary.review_reasons[issue.code] = (
                            summary.review_reasons.get(issue.code, 0) + 1
                        )

            for record in result.records:
                record_county = get_or_create_county(
                    session, record.county_name or county
                )
                row = persist_record(
                    session, record, source_file=source_file, county=record_county
                )
                new_records.append((record, row))
                summary.records_extracted += 1
                outcome.records += 1
                if not record.in_coverage:
                    summary.out_of_coverage += 1

            summary.files_processed += 1
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop a batch
            logger.exception("import failed for %s", path)
            outcome.status = "failed"
            outcome.error = str(exc)
            summary.files_failed += 1
        summary.files.append(outcome)

    # --- cluster ---------------------------------------------------------
    publishable = [
        (record, row)
        for record, row in new_records
        if record.in_coverage
        and classify_site(record.commodity_code, record.commodity).is_published
    ]
    summary.excluded_not_forestry = len(
        [
            record
            for record, _ in new_records
            if record.in_coverage
            and not classify_site(record.commodity_code, record.commodity).is_published
        ]
    )

    if publishable:
        records_only = [record for record, _ in publishable]
        row_by_index = {index: row for index, (_, row) in enumerate(publishable)}
        clustering = cluster_records(records_only, weights=weights)
        summary.clusters_created = len(clustering.clusters)

        for cluster in clustering.clusters:
            start, end = cluster.date_range
            county_row = get_or_create_county(session, cluster.county or county)
            slug_source = f"{cluster.title()} {start:%B %Y}" if start else cluster.title()
            cluster_row = ClusterRow(
                slug=_unique_slug(session, slugify(slug_source)),
                county_id=county_row.id if county_row else None,
                title=cluster.title(),
                title_basis=cluster.title_basis,
                owner_name=cluster.owner,
                owner_key=company_key(cluster.owner) or None,
                date_start=start,
                date_end=end,
                total_acres=cluster.total_acres,
                acreage_is_partial=cluster.acreage_is_partial,
                method=cluster.method,
                is_mixed_method=cluster.is_mixed_method,
                is_planned=cluster.is_planned,
                site_category=classify_site(
                    cluster.records[0].commodity_code, cluster.records[0].commodity
                ).category,
                confidence=str(cluster.confidence),
                review_reasons=cluster.review_reasons(),
                scoring={"pairs": [p.to_dict() for p in cluster.joining_pairs]},
                status="needs_review" if cluster.needs_review else "ready",
            )
            session.add(cluster_row)
            session.flush()

            for index in cluster.record_indices:
                session.add(
                    ClusterRecord(
                        cluster_id=cluster_row.id,
                        record_id=row_by_index[index].id,
                        assigned_by="auto",
                    )
                )

            if cluster.needs_review:
                summary.needs_review += 1
                for reason in cluster.review_reasons():
                    key = _reason_key(reason)
                    summary.review_reasons[key] = summary.review_reasons.get(key, 0) + 1
                    _add_review(
                        session,
                        entity_type="application_cluster",
                        entity_id=cluster_row.id,
                        reason=key,
                        detail=reason,
                        batch_id=batch.id,
                        options=[p.to_dict() for p in cluster.proposals] or None,
                    )
            else:
                summary.ready_to_publish += 1

    # --- chemicals -------------------------------------------------------
    # Runs after clustering so flags can be summarised onto each application.
    chemistry = chemicals_stage.run(
        session, record_ids=[row.id for _, row in new_records]
    )
    summary.chemical_alerts = chemistry.alerts
    if chemistry.products_unresolved:
        summary.review_reasons["unknown_product"] = (
            summary.review_reasons.get("unknown_product", 0) + chemistry.products_unresolved
        )
        for name in chemistry.unresolved_names:
            _add_review(
                session,
                entity_type="product",
                entity_id=0,
                reason="unknown_product",
                detail=f"{name} could not be identified from its EPA registration number",
                batch_id=batch.id,
            )
    if chemistry.products_seed_only:
        summary.review_reasons["unverified_product_data"] = chemistry.products_seed_only

    batch.state = "finished"
    batch.finished_at = datetime.now(UTC)
    batch.records_extracted = summary.records_extracted
    batch.clusters_created = summary.clusters_created
    batch.ready_count = summary.ready_to_publish
    batch.review_count = summary.needs_review
    batch.files_failed = summary.files_failed
    batch.summary = summary.to_dict()
    session.flush()
    return summary


def _unique_slug(session: Session, base: str) -> str:
    """Slugs are public URLs, so they must be stable and unique."""
    slug = base or "application"
    suffix = 2
    while session.scalar(select(ClusterRow.id).where(ClusterRow.slug == slug)) is not None:
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def _reason_key(reason: str) -> str:
    lowered = reason.lower()
    if "may belong" in lowered:
        return "possible_application_cluster"
    if "aerial and ground" in lowered:
        return "mixed_application_methods"
    if "parcel" in lowered:
        return "parcel_match"
    if "site id" in lowered:
        return "site_id_conflict"
    if "date" in lowered:
        return "unclear_date"
    if "product" in lowered:
        return "unknown_product"
    if "owner" in lowered or "operator" in lowered:
        return "missing_owner"
    return "needs_review"


def publishable_confidence(confidence: str) -> bool:
    return is_publishable(Confidence(confidence))
