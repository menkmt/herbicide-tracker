"""Canonical PUR record shape that every source format normalises into.

County agricultural departments publish pesticide-use data in whatever form
their software emits: tab-separated "use record" exports, per-application
"Pesticide Use Report" forms, scanned restricted-materials permits, county
spreadsheets.  The field *names*, the layout, and even whether the PLSS
location arrives packed into a site ID or spread across separate columns all
vary by county and by year.

Everything in :mod:`app.extraction` exists to turn that variety into the one
shape defined here, while recording per-field provenance so the public site can
always say which document a given fact came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.core.confidence import Confidence
from app.core.coverage import DocumentKind, coverage_note, in_coverage
from app.core.provenance import Provenance
from app.core.siteid import DecodedSiteId


class ApplicationMethod:
    """Normalised application methods.

    Sources write this as "Ground", "Aircraft", "Air", "Aerial", "Air/Ground",
    or a bare fume code.  The public grid only ever shows aerial vs ground, so
    that is what we normalise to.
    """

    GROUND = "ground"
    AERIAL = "aerial"
    OTHER = "other"
    UNKNOWN = "unknown"


_METHOD_ALIASES = {
    "ground": ApplicationMethod.GROUND,
    "grnd": ApplicationMethod.GROUND,
    "g": ApplicationMethod.GROUND,
    "aircraft": ApplicationMethod.AERIAL,
    "air": ApplicationMethod.AERIAL,
    "aerial": ApplicationMethod.AERIAL,
    "helicopter": ApplicationMethod.AERIAL,
    "fixed wing": ApplicationMethod.AERIAL,
    "a": ApplicationMethod.AERIAL,
    "other": ApplicationMethod.OTHER,
}


def normalize_method(value: str | None) -> str:
    if not value:
        return ApplicationMethod.UNKNOWN
    text = str(value).strip().lower()
    text = re.sub(r"[/,].*$", "", text).strip()  # "Air/Ground" -> "Air"
    return _METHOD_ALIASES.get(text, ApplicationMethod.OTHER if text else ApplicationMethod.UNKNOWN)


class IssueCode:
    """Reasons a record cannot be published without a human looking at it.

    These map one-to-one onto the review-queue reasons in the admin dashboard,
    so the queue can always explain itself in plain language.
    """

    UNDECODABLE_SITE_ID = "undecodable_site_id"
    SITE_ID_MTRS_CONFLICT = "site_id_mtrs_conflict"
    MISSING_LOCATION = "missing_location"
    MISSING_DATE = "missing_date"
    UNCLEAR_DATE = "unclear_date"
    MISSING_OWNER = "missing_owner"
    UNKNOWN_PRODUCT = "unknown_product"
    IMPLAUSIBLE_RATE = "implausible_rate"
    EXPIRED_REGISTRATION = "expired_registration"
    LOW_OCR_CONFIDENCE = "low_ocr_confidence"
    UNPARSED_SECTION = "unparsed_section"
    OUT_OF_COVERAGE = "out_of_coverage"
    NOI_WITHOUT_OUTCOME = "noi_without_outcome"


@dataclass
class DataIssue:
    """A specific, explainable problem found while reading a record."""

    code: str
    detail: str
    severity: str = "review"  # "review" blocks publication; "note" does not
    field_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "severity": self.severity,
            "field": self.field_name,
        }


@dataclass
class ProductApplication:
    """One pesticide product line on a use report.

    A single application commonly reports several products (a herbicide plus
    an adjuvant), each with its own quantity, so these are line items beneath
    a record rather than records in their own right.
    """

    product_name: str | None = None
    epa_reg_no: str | None = None
    quantity: float | None = None
    quantity_units: str | None = None
    #: Acres this specific product was applied to, when reported per product.
    treated_amount: float | None = None
    treated_units: str | None = None
    registration_expired: bool | None = None
    line_number: int | None = None

    @property
    def base_epa_reg_no(self) -> str | None:
        """EPA registration number without California's distributor suffix.

        DPR appends a two-letter distributor code -- ``2935-50176-AA`` is EPA
        registration ``2935-50176`` sold under a California sub-label.  Product
        lookups have to use the base number; the suffix is kept separately
        because it identifies the specific labelled product.
        """
        if not self.epa_reg_no:
            return None
        text = str(self.epa_reg_no).strip().upper()
        parts = text.split("-")
        # Base numbers are numeric segments; the suffix is alphabetic.
        while parts and not parts[-1].isdigit():
            parts.pop()
        return "-".join(parts) if parts else None

    @property
    def distributor_suffix(self) -> str | None:
        if not self.epa_reg_no:
            return None
        parts = str(self.epa_reg_no).strip().upper().split("-")
        return parts[-1] if parts and not parts[-1].isdigit() else None

    def rate_per_acre(self) -> float | None:
        """Applied quantity per treated acre, when both are known."""
        if self.quantity is None or not self.treated_amount:
            return None
        if self.treated_amount <= 0:
            return None
        return self.quantity / self.treated_amount

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_name": self.product_name,
            "epa_reg_no": self.epa_reg_no,
            "base_epa_reg_no": self.base_epa_reg_no,
            "distributor_suffix": self.distributor_suffix,
            "quantity": self.quantity,
            "quantity_units": self.quantity_units,
            "treated_amount": self.treated_amount,
            "treated_units": self.treated_units,
            "registration_expired": self.registration_expired,
            "line_number": self.line_number,
        }


@dataclass
class PurRecord:
    """One pesticide-use report: one site, one date, one or more products.

    The unit of a PUR record is the document number.  In a tabular export each
    product is its own row but several rows share a document number; those rows
    are one record with several :class:`ProductApplication` lines.
    """

    # --- identity -------------------------------------------------------
    #: Which of the three collected document kinds this record is.  A notice
    #: of intent describes a *planned* application and must never be presented
    #: as one that happened.
    record_kind: str = DocumentKind.USE_REPORT
    document_number: str | None = None
    permit_number: str | None = None
    county_name: str | None = None
    county_code: str | None = None
    site_district: str | None = None

    # --- who ------------------------------------------------------------
    operator_name: str | None = None
    applicator_name: str | None = None
    applicator_license: str | None = None
    applicator_license_type: str | None = None
    applicator_address: str | None = None
    #: Free-text "Location" / "Site Name", which in forestry PURs is usually
    #: the landowner or property name and is a strong parcel-matching signal.
    location_text: str | None = None

    # --- where ------------------------------------------------------------
    site_id: str | None = None
    site: DecodedSiteId | None = None
    mtrs_text: str | None = None

    # --- when ------------------------------------------------------------
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None
    application_date: date | None = None

    # --- what ------------------------------------------------------------
    method: str = ApplicationMethod.UNKNOWN
    method_raw: str | None = None
    commodity: str | None = None
    commodity_code: str | None = None
    planted_amount: float | None = None
    planted_units: str | None = None
    treated_amount: float | None = None
    treated_units: str | None = None
    products: list[ProductApplication] = field(default_factory=list)

    # --- bookkeeping ------------------------------------------------------
    submittal_status: str | None = None
    school_notification: str | None = None
    #: NOIs only: the document number of the use report that later confirmed
    #: this application actually took place, when one has been matched.
    fulfilled_by_document: str | None = None
    #: Per-field provenance, keyed by attribute name.
    field_sources: dict[str, Provenance] = field(default_factory=dict)
    issues: list[DataIssue] = field(default_factory=list)
    #: Untouched source values, so nothing read from the document is ever lost.
    raw: dict[str, Any] = field(default_factory=dict)
    source_profile: str | None = None

    # ---------------------------------------------------------------- utils
    def add_issue(
        self, code: str, detail: str, *, severity: str = "review", field_name: str | None = None
    ) -> None:
        self.issues.append(DataIssue(code, detail, severity=severity, field_name=field_name))

    @property
    def needs_review(self) -> bool:
        return any(i.severity == "review" for i in self.issues)

    @property
    def is_notice_of_intent(self) -> bool:
        return self.record_kind == DocumentKind.NOTICE_OF_INTENT

    @property
    def is_planned(self) -> bool:
        """True when this describes an intended application, not a reported one.

        An NOI that has been matched to a subsequent use report is no longer
        merely planned — the use report is the evidence it happened.
        """
        return self.is_notice_of_intent and not self.fulfilled_by_document

    @property
    def kind_label(self) -> str:
        return DocumentKind.label(self.record_kind)

    @property
    def in_coverage(self) -> bool:
        """Whether this record falls inside the tracker's published window."""
        start, _ = self.date_range
        return in_coverage(start)

    def check_coverage(self) -> None:
        """Flag the record when it falls outside the published window.

        Out-of-scope records are kept — the source document is preserved and
        the record stays queryable — but they are held back from publication
        with an explicit reason rather than silently dropped.
        """
        start, _ = self.date_range
        note = coverage_note(start)
        if note is not None and start is not None:
            self.add_issue(
                IssueCode.OUT_OF_COVERAGE,
                f"outside the tracker's coverage: {note}",
                field_name="application_date",
            )

    @property
    def date_range(self) -> tuple[date | None, date | None]:
        start = self.start_datetime.date() if self.start_datetime else self.application_date
        end = self.end_datetime.date() if self.end_datetime else start
        return start, end

    @property
    def mtrs(self) -> str | None:
        return self.site.mtrs if self.site else None

    @property
    def product_names(self) -> list[str]:
        return [p.product_name for p in self.products if p.product_name]

    def source_for(self, field_name: str) -> Provenance | None:
        return self.field_sources.get(field_name)

    def confidence(self) -> Confidence:
        """Overall confidence, limited by the weakest sourced field."""
        levels = [Confidence(p.confidence) for p in self.field_sources.values()]
        if self.site:
            levels.append(Confidence(self.site.confidence))
        if not levels:
            return Confidence.LOW
        from app.core.confidence import weakest

        return weakest(*levels)

    def to_dict(self) -> dict[str, Any]:
        start, end = self.date_range
        return {
            "record_kind": self.record_kind,
            "kind_label": self.kind_label,
            "is_planned": self.is_planned,
            "fulfilled_by_document": self.fulfilled_by_document,
            "in_coverage": self.in_coverage,
            "document_number": self.document_number,
            "permit_number": self.permit_number,
            "county_name": self.county_name,
            "county_code": self.county_code,
            "site_district": self.site_district,
            "operator_name": self.operator_name,
            "applicator_name": self.applicator_name,
            "applicator_license": self.applicator_license,
            "applicator_license_type": self.applicator_license_type,
            "applicator_address": self.applicator_address,
            "location_text": self.location_text,
            "site_id": self.site_id,
            "mtrs": self.mtrs,
            "site": self.site.to_dict() if self.site else None,
            "start_datetime": self.start_datetime.isoformat() if self.start_datetime else None,
            "end_datetime": self.end_datetime.isoformat() if self.end_datetime else None,
            "date_start": start.isoformat() if start else None,
            "date_end": end.isoformat() if end else None,
            "method": self.method,
            "method_raw": self.method_raw,
            "commodity": self.commodity,
            "commodity_code": self.commodity_code,
            "planted_amount": self.planted_amount,
            "planted_units": self.planted_units,
            "treated_amount": self.treated_amount,
            "treated_units": self.treated_units,
            "products": [p.to_dict() for p in self.products],
            "submittal_status": self.submittal_status,
            "school_notification": self.school_notification,
            "issues": [i.to_dict() for i in self.issues],
            "needs_review": self.needs_review,
            "confidence": str(self.confidence()),
            "source_profile": self.source_profile,
            "field_sources": {k: v.to_dict() for k, v in self.field_sources.items()},
        }


@dataclass
class PermitSite:
    """One row of a restricted-materials permit's SITES LIST.

    County permits enumerate every site the operator may treat, with the site
    ID, its MTRS, the permitted acreage and the restricted materials allowed
    there.  That makes the permit an independent check on the use reports: it
    confirms the site ID decode and supplies an authoritative acreage.
    """

    site_id: str | None = None
    mtrs_text: str | None = None
    site: DecodedSiteId | None = None
    site_name: str | None = None
    district: str | None = None
    commodity: str | None = None
    commodity_code: str | None = None
    acreage: float | None = None
    acreage_units: str | None = None
    #: Restricted materials permitted on this site, e.g. ``[("636", "2,4-D")]``.
    permitted_materials: list[tuple[str, str]] = field(default_factory=list)
    issues: list[DataIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "site_id": self.site_id,
            "mtrs_text": self.mtrs_text,
            "mtrs": self.site.mtrs if self.site else None,
            "site": self.site.to_dict() if self.site else None,
            "site_name": self.site_name,
            "district": self.district,
            "commodity": self.commodity,
            "commodity_code": self.commodity_code,
            "acreage": self.acreage,
            "acreage_units": self.acreage_units,
            "permitted_materials": [list(m) for m in self.permitted_materials],
            "issues": [i.to_dict() for i in self.issues],
        }


@dataclass
class PermitContact:
    """One row of a permit's CONTACT LIST.

    This is the tracker's best structured source for who is involved in an
    application and under which licence, and it is what seeds the company and
    person profiles.
    """

    name: str | None = None
    phone: str | None = None
    license_number: str | None = None
    license_expiration: date | None = None
    contact_type: str | None = None
    #: True when the contact is a business rather than a named individual.
    is_business: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "phone": self.phone,
            "license_number": self.license_number,
            "license_expiration": (
                self.license_expiration.isoformat() if self.license_expiration else None
            ),
            "contact_type": self.contact_type,
            "is_business": self.is_business,
        }


@dataclass
class PermitRecord:
    """A county restricted-materials permit."""

    permit_number: str | None = None
    operator_name: str | None = None
    operator_id: str | None = None
    county_name: str | None = None
    county_district: str | None = None
    agent_name: str | None = None
    mailing_address: str | None = None
    primary_phone: str | None = None
    issued_on: date | None = None
    valid_from: date | None = None
    expires_on: date | None = None
    permit_duration: str | None = None
    type_of_use: str | None = None
    applicant_name: str | None = None
    applicant_title: str | None = None
    contacts: list[PermitContact] = field(default_factory=list)
    #: Restricted materials the permit authorises, ``[(number, name, methods)]``.
    permitted_materials: list[dict[str, Any]] = field(default_factory=list)
    sites: list[PermitSite] = field(default_factory=list)
    field_sources: dict[str, Provenance] = field(default_factory=dict)
    issues: list[DataIssue] = field(default_factory=list)
    source_profile: str | None = None

    @property
    def needs_review(self) -> bool:
        return any(i.severity == "review" for i in self.issues)

    def site_index(self) -> dict[str, PermitSite]:
        """Site ID -> permitted site, for cross-checking use reports."""
        return {s.site_id: s for s in self.sites if s.site_id}

    def to_dict(self) -> dict[str, Any]:
        return {
            "permit_number": self.permit_number,
            "operator_name": self.operator_name,
            "operator_id": self.operator_id,
            "county_name": self.county_name,
            "county_district": self.county_district,
            "agent_name": self.agent_name,
            "mailing_address": self.mailing_address,
            "primary_phone": self.primary_phone,
            "issued_on": self.issued_on.isoformat() if self.issued_on else None,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "expires_on": self.expires_on.isoformat() if self.expires_on else None,
            "permit_duration": self.permit_duration,
            "type_of_use": self.type_of_use,
            "applicant_name": self.applicant_name,
            "applicant_title": self.applicant_title,
            "contacts": [c.to_dict() for c in self.contacts],
            "permitted_materials": self.permitted_materials,
            "sites": [s.to_dict() for s in self.sites],
            "issues": [i.to_dict() for i in self.issues],
            "needs_review": self.needs_review,
            "source_profile": self.source_profile,
            "field_sources": {k: v.to_dict() for k, v in self.field_sources.items()},
        }


@dataclass
class ExtractionResult:
    """Everything one uploaded file yielded."""

    source_name: str
    sha256: str | None = None
    profile: str | None = None
    records: list[PurRecord] = field(default_factory=list)
    permits: list[PermitRecord] = field(default_factory=list)
    #: Enforcement documents (NOPAs, decisions). Typed as Any to avoid a
    #: circular import; see app.extraction.enforcement_doc.
    enforcement: list[Any] = field(default_factory=list)
    #: County investigation reports; see app.extraction.investigation.
    investigations: list[Any] = field(default_factory=list)
    #: The document's extracted text, kept so the importer can store it
    #: without reading — or OCR'ing — the original a second time.
    document_text: str | None = None
    #: Non-fatal problems with the file as a whole (unreadable page, etc.).
    issues: list[DataIssue] = field(default_factory=list)
    #: Free-form notes about how the file was read, shown in the import summary.
    notes: list[str] = field(default_factory=list)

    @property
    def record_count(self) -> int:
        return len(self.records)

    @property
    def review_count(self) -> int:
        return sum(1 for r in self.records if r.needs_review) + sum(
            1 for p in self.permits if p.needs_review
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "sha256": self.sha256,
            "profile": self.profile,
            "records": [r.to_dict() for r in self.records],
            "permits": [p.to_dict() for p in self.permits],
            "enforcement": [e.to_dict() for e in self.enforcement],
            "investigations": [i.to_dict() for i in self.investigations],
            "issues": [i.to_dict() for i in self.issues],
            "notes": list(self.notes),
            "record_count": self.record_count,
            "review_count": self.review_count,
        }
