"""Database model.

Designed statewide from the start: every table that can vary by county is
keyed by county, and nothing assumes Lassen.  Three principles shape the
schema:

**Source records are immutable.**  ``SourceFile`` rows hold the uploaded
document and its hash; ``PurRecord`` rows hold exactly what the document said.
Clustering, enrichment and publication all produce *new* rows that reference
these, so no county data is ever overwritten by a derived conclusion.

**Every fact knows its source.**  ``FactSource`` is a generic provenance table
keyed by (entity type, entity id, field), so any published value can be traced
to a document, a dataset and a retrieval date.

**Geography is three separate things.**  The PLSS section a PUR reports, the
parcel polygons owned by the operator, and a forestry project's boundary are
different claims about where something happened.  They get different tables and
are never silently substituted for one another.
"""

from __future__ import annotations

from datetime import date, datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


#: WGS84. Geometries are stored in 4326 and measured in a projected CRS at
#: query time, so distances are correct without a second stored copy.
SRID = 4326

# GeoAlchemy2 creates a GiST index for every Geometry column automatically, so
# geometry columns deliberately carry no explicit Index() here: declaring one
# produces a second, identical index that only costs write throughput.


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Geography and counties
# ---------------------------------------------------------------------------

class County(Base, TimestampMixin):
    __tablename__ = "counties"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    #: California county FIPS code.
    fips: Mapped[str | None] = mapped_column(String(8))
    #: The county's DPR district number, printed on its permits (Lassen is 18).
    ag_district: Mapped[str | None] = mapped_column(String(8))
    ag_department_name: Mapped[str | None] = mapped_column(String(160))
    ag_department_email: Mapped[str | None] = mapped_column(String(160))
    ag_department_phone: Mapped[str | None] = mapped_column(String(40))
    #: Default PLSS direction letters for decoding this county's site IDs.
    default_meridian: Mapped[str] = mapped_column(String(1), default="M")
    default_township_dir: Mapped[str] = mapped_column(String(1), default="N")
    default_range_dir: Mapped[str] = mapped_column(String(1), default="E")
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    boundary = mapped_column(Geometry("MULTIPOLYGON", srid=SRID), nullable=True)

    records: Mapped[list[PurRecord]] = relationship(back_populates="county")


class PlssSection(Base, TimestampMixin):
    """A PLSS section polygon — the geography a PUR site ID refers to.

    This is *reported* geography.  It says which square mile the county was
    told about; it does not say that the whole square mile was treated.
    """

    __tablename__ = "plss_sections"
    __table_args__ = (
        UniqueConstraint("mtrs", name="uq_plss_mtrs"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    mtrs: Mapped[str] = mapped_column(String(16), nullable=False)
    meridian: Mapped[str] = mapped_column(String(1), nullable=False)
    township: Mapped[int] = mapped_column(Integer, nullable=False)
    township_dir: Mapped[str] = mapped_column(String(1), nullable=False)
    range: Mapped[int] = mapped_column(Integer, nullable=False)
    range_dir: Mapped[str] = mapped_column(String(1), nullable=False)
    section: Mapped[int] = mapped_column(Integer, nullable=False)
    county_id: Mapped[int | None] = mapped_column(ForeignKey("counties.id"))
    geom = mapped_column(Geometry("MULTIPOLYGON", srid=SRID), nullable=True)
    source: Mapped[str | None] = mapped_column(String(120))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Parcel(Base, TimestampMixin):
    """An assessor parcel — *property* geography, distinct from PLSS."""

    __tablename__ = "parcels"
    __table_args__ = (
        UniqueConstraint("county_id", "apn", name="uq_parcel_county_apn"),
        Index("ix_parcel_owner_key", "owner_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    county_id: Mapped[int] = mapped_column(ForeignKey("counties.id"), nullable=False)
    apn: Mapped[str] = mapped_column(String(48), nullable=False)
    owner_name: Mapped[str | None] = mapped_column(String(255))
    #: Normalised owner name, for matching against PUR operators.
    owner_key: Mapped[str | None] = mapped_column(String(255))
    situs_address: Mapped[str | None] = mapped_column(String(255))
    acreage: Mapped[float | None] = mapped_column(Float)
    use_code: Mapped[str | None] = mapped_column(String(64))
    geom = mapped_column(Geometry("MULTIPOLYGON", srid=SRID), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Source documents
# ---------------------------------------------------------------------------

class SourceFile(Base, TimestampMixin):
    """An uploaded or CPRA-delivered document.  Immutable once processed."""

    __tablename__ = "source_files"
    __table_args__ = (UniqueConstraint("sha256", name="uq_source_sha256"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(128))
    #: Where the original bytes live.
    #:
    #: ``local``      the tracker holds the file in its own object storage.
    #: ``inquisitor`` the original stays in Inquisitor's evidence vault, which
    #:                already keeps immutable originals with hashes and a
    #:                chain of custody. Duplicating multi-gigabyte scans into
    #:                a second store would give two systems of record for the
    #:                same document and no benefit.
    #: ``none``       the original is no longer held anywhere reachable.
    storage_mode: Mapped[str] = mapped_column(String(16), default="local")
    #: Empty when the original is held elsewhere.
    storage_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: URL the original can be fetched from when it lives in Inquisitor.
    origin_url: Mapped[str | None] = mapped_column(Text)
    #: The document's text, as extracted or OCR'd.
    #:
    #: Kept in the database because it is small — a 6MB scanned permit yields
    #: about 25KB of text — and because it is what the tracker actually needs
    #: at read time: searching, citing a passage, or showing the paragraph a
    #: published fact came from. Re-OCRing a scan to answer a page request
    #: would take a minute; reading a text column takes a millisecond.
    extracted_text: Mapped[str | None] = mapped_column(Text)
    text_bytes: Mapped[int | None] = mapped_column(Integer)
    county_id: Mapped[int | None] = mapped_column(ForeignKey("counties.id"))
    #: Which extraction profile read it.
    profile: Mapped[str | None] = mapped_column(String(64))
    document_kind: Mapped[str | None] = mapped_column(String(32))
    #: Where it came from: an admin upload or an Inquisitor CPRA production.
    origin: Mapped[str] = mapped_column(String(32), default="upload")
    #: CPRA chain of custody, when the file arrived that way.
    cpra_agency: Mapped[str | None] = mapped_column(String(255))
    cpra_request_number: Mapped[str | None] = mapped_column(String(64))
    cpra_production: Mapped[str | None] = mapped_column(String(64))
    inquisitor_source_id: Mapped[str | None] = mapped_column(String(64))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_state: Mapped[str] = mapped_column(String(32), default="pending")
    processing_notes: Mapped[dict | None] = mapped_column(JSON)
    used_ocr: Mapped[bool] = mapped_column(Boolean, default=False)

    records: Mapped[list[PurRecord]] = relationship(back_populates="source_file")


class FactSource(Base, TimestampMixin):
    """Provenance for a single field on a single row.

    Generic on purpose: adding a new enriched entity should not require a new
    provenance table, and a reviewer should be able to ask "where did this
    value come from?" about anything the site publishes.
    """

    __tablename__ = "fact_sources"
    __table_args__ = (
        Index("ix_fact_entity", "entity_type", "entity_id", "field_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(128))
    source_url: Mapped[str | None] = mapped_column(Text)
    source_file_id: Mapped[int | None] = mapped_column(ForeignKey("source_files.id"))
    locator: Mapped[str | None] = mapped_column(Text)
    extraction_method: Mapped[str] = mapped_column(String(48), nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[dict | None] = mapped_column(JSON)


# ---------------------------------------------------------------------------
# Permits
# ---------------------------------------------------------------------------

class Permit(Base, TimestampMixin):
    __tablename__ = "permits"
    __table_args__ = (UniqueConstraint("permit_number", name="uq_permit_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    permit_number: Mapped[str] = mapped_column(String(48), nullable=False)
    county_id: Mapped[int | None] = mapped_column(ForeignKey("counties.id"))
    operator_name: Mapped[str | None] = mapped_column(String(255))
    operator_key: Mapped[str | None] = mapped_column(String(255))
    operator_id: Mapped[str | None] = mapped_column(String(48))
    agent_name: Mapped[str | None] = mapped_column(String(160))
    applicant_name: Mapped[str | None] = mapped_column(String(160))
    applicant_title: Mapped[str | None] = mapped_column(String(255))
    issued_on: Mapped[date | None] = mapped_column(Date)
    valid_from: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date)
    permit_duration: Mapped[str | None] = mapped_column(String(64))
    type_of_use: Mapped[str | None] = mapped_column(String(64))
    source_file_id: Mapped[int | None] = mapped_column(ForeignKey("source_files.id"))

    sites: Mapped[list[PermitSite]] = relationship(
        back_populates="permit", cascade="all, delete-orphan"
    )
    contacts: Mapped[list[PermitContact]] = relationship(
        back_populates="permit", cascade="all, delete-orphan"
    )
    materials: Mapped[list[PermitMaterial]] = relationship(
        back_populates="permit", cascade="all, delete-orphan"
    )


class PermitSite(Base, TimestampMixin):
    """A site the permit authorises, with its permitted acreage."""

    __tablename__ = "permit_sites"
    __table_args__ = (Index("ix_permit_site_mtrs", "mtrs"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    permit_id: Mapped[int] = mapped_column(ForeignKey("permits.id"), nullable=False)
    site_id: Mapped[str | None] = mapped_column(String(32))
    mtrs: Mapped[str | None] = mapped_column(String(16))
    mtrs_text: Mapped[str | None] = mapped_column(String(32))
    site_name: Mapped[str | None] = mapped_column(String(255))
    district: Mapped[str | None] = mapped_column(String(8))
    commodity: Mapped[str | None] = mapped_column(String(120))
    commodity_code: Mapped[str | None] = mapped_column(String(16))
    permitted_acreage: Mapped[float | None] = mapped_column(Float)
    permitted_materials: Mapped[list | None] = mapped_column(JSON)
    has_conflict: Mapped[bool] = mapped_column(Boolean, default=False)

    permit: Mapped[Permit] = relationship(back_populates="sites")


class PermitMaterial(Base, TimestampMixin):
    """A restricted material the permit authorises.

    Doubles as the tracker's most defensible evidence that a chemical is a
    California restricted material: the county said so, on this permit.
    """

    __tablename__ = "permit_materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    permit_id: Mapped[int] = mapped_column(ForeignKey("permits.id"), nullable=False)
    number: Mapped[str | None] = mapped_column(String(8))
    name: Mapped[str | None] = mapped_column(String(160))
    pests: Mapped[str | None] = mapped_column(String(120))
    form: Mapped[str | None] = mapped_column(String(64))
    methods: Mapped[str | None] = mapped_column(String(64))
    applicators: Mapped[str | None] = mapped_column(String(32))

    permit: Mapped[Permit] = relationship(back_populates="materials")


class PermitContact(Base, TimestampMixin):
    __tablename__ = "permit_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    permit_id: Mapped[int] = mapped_column(ForeignKey("permits.id"), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(40))
    license_number: Mapped[str | None] = mapped_column(String(32))
    license_expiration: Mapped[date | None] = mapped_column(Date)
    contact_type: Mapped[str | None] = mapped_column(String(64))
    is_business: Mapped[bool] = mapped_column(Boolean, default=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"))

    permit: Mapped[Permit] = relationship(back_populates="contacts")


# ---------------------------------------------------------------------------
# PUR records
# ---------------------------------------------------------------------------

class PurRecord(Base, TimestampMixin):
    """Exactly what one use report or notice of intent said.  Never edited."""

    __tablename__ = "pur_records"
    __table_args__ = (
        UniqueConstraint("source_file_id", "document_number", "site_id", "date_start",
                         name="uq_pur_record_identity"),
        Index("ix_pur_mtrs", "mtrs"),
        Index("ix_pur_dates", "date_start", "date_end"),
        Index("ix_pur_operator_key", "operator_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("source_files.id"), nullable=False)
    county_id: Mapped[int | None] = mapped_column(ForeignKey("counties.id"))

    record_kind: Mapped[str] = mapped_column(String(24), default="use_report")
    document_number: Mapped[str | None] = mapped_column(String(48))
    permit_number: Mapped[str | None] = mapped_column(String(48))
    permit_id: Mapped[int | None] = mapped_column(ForeignKey("permits.id"))
    site_district: Mapped[str | None] = mapped_column(String(8))

    operator_name: Mapped[str | None] = mapped_column(String(255))
    operator_key: Mapped[str | None] = mapped_column(String(255))
    applicator_name: Mapped[str | None] = mapped_column(String(255))
    applicator_key: Mapped[str | None] = mapped_column(String(255))
    applicator_license: Mapped[str | None] = mapped_column(String(32))
    applicator_license_type: Mapped[str | None] = mapped_column(String(16))
    applicator_address: Mapped[str | None] = mapped_column(Text)
    pca_name: Mapped[str | None] = mapped_column(String(255))
    #: Free-text location/site name; often the landowner in forestry PURs.
    location_text: Mapped[str | None] = mapped_column(String(255))

    site_id: Mapped[str | None] = mapped_column(String(32))
    mtrs: Mapped[str | None] = mapped_column(String(16))
    plss_section_id: Mapped[int | None] = mapped_column(ForeignKey("plss_sections.id"))
    site_decode_method: Mapped[str | None] = mapped_column(String(48))
    site_confidence: Mapped[str | None] = mapped_column(String(16))

    date_start: Mapped[date | None] = mapped_column(Date)
    date_end: Mapped[date | None] = mapped_column(Date)
    start_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    method: Mapped[str] = mapped_column(String(16), default="unknown")
    method_raw: Mapped[str | None] = mapped_column(String(64))
    commodity: Mapped[str | None] = mapped_column(String(120))
    commodity_code: Mapped[str | None] = mapped_column(String(16))
    site_category: Mapped[str | None] = mapped_column(String(32))
    site_category_confidence: Mapped[str | None] = mapped_column(String(16))
    planted_amount: Mapped[float | None] = mapped_column(Float)
    planted_units: Mapped[str | None] = mapped_column(String(24))
    treated_amount: Mapped[float | None] = mapped_column(Float)
    treated_units: Mapped[str | None] = mapped_column(String(24))

    submittal_status: Mapped[str | None] = mapped_column(String(48))
    school_notification: Mapped[str | None] = mapped_column(String(48))
    #: NOIs only: the use report that later confirmed the application happened.
    fulfilled_by_record_id: Mapped[int | None] = mapped_column(ForeignKey("pur_records.id"))

    in_coverage: Mapped[bool] = mapped_column(Boolean, default=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    issues: Mapped[list | None] = mapped_column(JSON)
    raw: Mapped[dict | None] = mapped_column(JSON)

    source_file: Mapped[SourceFile] = relationship(back_populates="records")
    county: Mapped[County | None] = relationship(back_populates="records")
    products: Mapped[list[PurProduct]] = relationship(
        back_populates="record", cascade="all, delete-orphan"
    )


class PurProduct(Base, TimestampMixin):
    """One product line on a use report."""

    __tablename__ = "pur_products"
    __table_args__ = (Index("ix_pur_product_reg", "base_epa_reg_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("pur_records.id"), nullable=False)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    product_name: Mapped[str | None] = mapped_column(String(255))
    epa_reg_no: Mapped[str | None] = mapped_column(String(48))
    base_epa_reg_no: Mapped[str | None] = mapped_column(String(48))
    distributor_suffix: Mapped[str | None] = mapped_column(String(8))
    quantity: Mapped[float | None] = mapped_column(Numeric(16, 4))
    quantity_units: Mapped[str | None] = mapped_column(String(24))
    treated_amount: Mapped[float | None] = mapped_column(Float)
    treated_units: Mapped[str | None] = mapped_column(String(24))
    registration_expired: Mapped[bool | None] = mapped_column(Boolean)
    #: Quantity resolved onto a canonical unit; null when it could not be.
    gallons: Mapped[float | None] = mapped_column(Float)
    pounds: Mapped[float | None] = mapped_column(Float)

    record: Mapped[PurRecord] = relationship(back_populates="products")


# ---------------------------------------------------------------------------
# Application clusters — the public unit
# ---------------------------------------------------------------------------

class ApplicationCluster(Base, TimestampMixin):
    """One public application: the parent of several PUR records."""

    __tablename__ = "application_clusters"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_cluster_slug"),
        Index("ix_cluster_dates", "date_start", "date_end"),
        Index("ix_cluster_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    county_id: Mapped[int | None] = mapped_column(ForeignKey("counties.id"))

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    title_basis: Mapped[str | None] = mapped_column(String(160))
    title_override: Mapped[str | None] = mapped_column(String(255))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))

    owner_name: Mapped[str | None] = mapped_column(String(255))
    owner_key: Mapped[str | None] = mapped_column(String(255))
    landowner_company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))

    date_start: Mapped[date | None] = mapped_column(Date)
    date_end: Mapped[date | None] = mapped_column(Date)
    total_acres: Mapped[float | None] = mapped_column(Float)
    acreage_is_partial: Mapped[bool] = mapped_column(Boolean, default=False)
    method: Mapped[str] = mapped_column(String(16), default="unknown")
    is_mixed_method: Mapped[bool] = mapped_column(Boolean, default=False)
    is_planned: Mapped[bool] = mapped_column(Boolean, default=False)
    site_category: Mapped[str | None] = mapped_column(String(32))

    #: Union of this application's parcel polygons, used for radius search.
    geom = mapped_column(Geometry("MULTIPOLYGON", srid=SRID), nullable=True)

    #: ready | needs_review | published | rejected
    status: Mapped[str] = mapped_column(String(24), default="needs_review")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[str] = mapped_column(String(16), default="medium")
    flags: Mapped[dict | None] = mapped_column(JSON)
    review_reasons: Mapped[list | None] = mapped_column(JSON)
    scoring: Mapped[dict | None] = mapped_column(JSON)

    members: Mapped[list[ClusterRecord]] = relationship(
        back_populates="cluster", cascade="all, delete-orphan"
    )
    parcels: Mapped[list[ClusterParcel]] = relationship(
        back_populates="cluster", cascade="all, delete-orphan"
    )


class ClusterRecord(Base):
    """Membership of a PUR record in an application cluster."""

    __tablename__ = "application_cluster_records"
    __table_args__ = (
        UniqueConstraint("cluster_id", "record_id", name="uq_cluster_record"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("application_clusters.id"), nullable=False)
    record_id: Mapped[int] = mapped_column(ForeignKey("pur_records.id"), nullable=False)
    #: "auto" or "manual" — a record moved by an administrator stays moved.
    assigned_by: Mapped[str] = mapped_column(String(16), default="auto")
    score: Mapped[int | None] = mapped_column(Integer)
    explanation: Mapped[str | None] = mapped_column(Text)

    cluster: Mapped[ApplicationCluster] = relationship(back_populates="members")


class ClusterParcel(Base):
    """A parcel associated with an application.

    The association is explicitly "this parcel is connected to this
    application", never "this parcel was sprayed" — the match basis is
    recorded so the public page can say how the link was made.
    """

    __tablename__ = "application_parcels"
    __table_args__ = (UniqueConstraint("cluster_id", "parcel_id", name="uq_cluster_parcel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("application_clusters.id"), nullable=False)
    parcel_id: Mapped[int] = mapped_column(ForeignKey("parcels.id"), nullable=False)
    match_basis: Mapped[str | None] = mapped_column(String(160))
    confidence: Mapped[str] = mapped_column(String(16), default="medium")
    owner_match_score: Mapped[float | None] = mapped_column(Float)

    cluster: Mapped[ApplicationCluster] = relationship(back_populates="parcels")


class Project(Base, TimestampMixin):
    """A CAL FIRE / CalTREES forestry project."""

    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("identifier", name="uq_project_identifier"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    identifier: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    kind: Mapped[str | None] = mapped_column(String(48))  # THP, exemption, NTMP...
    county_id: Mapped[int | None] = mapped_column(ForeignKey("counties.id"))
    landowner_name: Mapped[str | None] = mapped_column(String(255))
    filed_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(String(48))
    source_url: Mapped[str | None] = mapped_column(Text)
    geom = mapped_column(Geometry("MULTIPOLYGON", srid=SRID), nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Chemicals
# ---------------------------------------------------------------------------

class ActiveIngredient(Base, TimestampMixin):
    __tablename__ = "active_ingredients"
    __table_args__ = (UniqueConstraint("slug", name="uq_ingredient_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    cas_number: Mapped[str | None] = mapped_column(String(32))
    chemical_class: Mapped[str | None] = mapped_column(String(120))
    pesticide_type: Mapped[str | None] = mapped_column(String(64))
    #: Sourced narrative sections for the chemical page.
    overview: Mapped[str | None] = mapped_column(Text)
    groundwater: Mapped[str | None] = mapped_column(Text)
    surface_water: Mapped[str | None] = mapped_column(Text)
    persistence: Mapped[str | None] = mapped_column(Text)
    ecological: Mapped[str | None] = mapped_column(Text)
    human_health: Mapped[str | None] = mapped_column(Text)
    is_california_restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_watchlisted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)


class Product(Base, TimestampMixin):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("base_epa_reg_no", name="uq_product_reg"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    base_epa_reg_no: Mapped[str] = mapped_column(String(48), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    slug: Mapped[str | None] = mapped_column(String(255))
    registrant: Mapped[str | None] = mapped_column(String(255))
    formulation: Mapped[str | None] = mapped_column(String(64))
    signal_word: Mapped[str | None] = mapped_column(String(32))
    density_lb_per_gallon: Mapped[float | None] = mapped_column(Float)
    is_adjuvant: Mapped[bool] = mapped_column(Boolean, default=False)
    #: surfactant | crop_oil | marker_dye | drift_control | ... See
    #: app.chemicals.adjuvants. Null for products that are pesticides.
    adjuvant_type: Mapped[str | None] = mapped_column(String(32))
    federal_restricted_use: Mapped[bool | None] = mapped_column(Boolean)
    california_restricted: Mapped[bool | None] = mapped_column(Boolean)
    #: verified | seed | unresolved — gates automatic publication.
    verification: Mapped[str] = mapped_column(String(16), default="unresolved")
    label_url: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ingredients: Mapped[list[ProductIngredient]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductIngredient(Base):
    __tablename__ = "product_ingredients"
    __table_args__ = (
        UniqueConstraint("product_id", "ingredient_id", name="uq_product_ingredient"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("active_ingredients.id"), nullable=False)
    percent: Mapped[float | None] = mapped_column(Float)

    product: Mapped[Product] = relationship(back_populates="ingredients")
    ingredient: Mapped[ActiveIngredient] = relationship()


class ChemicalFlagRow(Base, TimestampMixin):
    """A stored warning flag, always with the source that justifies it."""

    __tablename__ = "chemical_flags"
    __table_args__ = (
        Index("ix_flag_subject", "subject_type", "subject_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)  # product|ingredient
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_name: Mapped[str] = mapped_column(String(160), nullable=False)
    level: Mapped[str] = mapped_column(String(12), nullable=False)
    reason: Mapped[str] = mapped_column(String(48), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    is_regulatory: Mapped[bool] = mapped_column(Boolean, default=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(16), default="high")


# ---------------------------------------------------------------------------
# People and companies
# ---------------------------------------------------------------------------

class Company(Base, TimestampMixin):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_company_slug"),
        Index("ix_company_key", "name_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_key: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    business_type: Mapped[str | None] = mapped_column(String(64))
    dpr_license: Mapped[str | None] = mapped_column(String(32))
    license_status: Mapped[str | None] = mapped_column(String(48))
    license_expiration: Mapped[date | None] = mapped_column(Date)
    business_address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(160))
    website: Mapped[str | None] = mapped_column(Text)
    #: Enriched contact details are held back until an administrator approves.
    contact_review_state: Mapped[str] = mapped_column(String(24), default="none")
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)

    aliases: Mapped[list[CompanyAlias]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class CompanyAlias(Base):
    __tablename__ = "company_aliases"
    __table_args__ = (UniqueConstraint("company_id", "alias", name="uq_company_alias"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    seen_in: Mapped[str | None] = mapped_column(String(255))

    company: Mapped[Company] = relationship(back_populates="aliases")


class Person(Base, TimestampMixin):
    __tablename__ = "people"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_person_slug"),
        Index("ix_person_key", "name_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    name_key: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    given_name: Mapped[str | None] = mapped_column(String(80))
    family_name: Mapped[str | None] = mapped_column(String(80))
    employer_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    job_title: Mapped[str | None] = mapped_column(String(160))
    business_phone: Mapped[str | None] = mapped_column(String(40))
    business_email: Mapped[str | None] = mapped_column(String(160))
    business_address: Mapped[str | None] = mapped_column(Text)
    photo_url: Mapped[str | None] = mapped_column(Text)
    photo_source: Mapped[str | None] = mapped_column(Text)
    contact_review_state: Mapped[str] = mapped_column(String(24), default="none")
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)

    licenses: Mapped[list[License]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )


class License(Base, TimestampMixin):
    __tablename__ = "licenses"
    __table_args__ = (
        UniqueConstraint("license_type", "number", name="uq_license_type_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    license_type: Mapped[str] = mapped_column(String(24), nullable=False)
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(32))
    expires_on: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(Text)

    person: Mapped[Person | None] = relationship(back_populates="licenses")


class ClusterParty(Base):
    """A person or company's role in an application."""

    __tablename__ = "application_parties"
    __table_args__ = (
        UniqueConstraint("cluster_id", "role", "company_id", "person_id",
                         name="uq_cluster_party"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("application_clusters.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"))


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

class ImportBatch(Base, TimestampMixin):
    """One drag-and-drop import, so the admin can see a summary per run."""

    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str | None] = mapped_column(String(255))
    origin: Mapped[str] = mapped_column(String(32), default="upload")
    state: Mapped[str] = mapped_column(String(24), default="running")
    files_total: Mapped[int] = mapped_column(Integer, default=0)
    files_failed: Mapped[int] = mapped_column(Integer, default=0)
    records_extracted: Mapped[int] = mapped_column(Integer, default=0)
    clusters_created: Mapped[int] = mapped_column(Integer, default=0)
    ready_count: Mapped[int] = mapped_column(Integer, default=0)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict | None] = mapped_column(JSON)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewItem(Base, TimestampMixin):
    """Something a human has to decide."""

    __tablename__ = "review_items"
    __table_args__ = (Index("ix_review_state", "state", "reason"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(48), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    #: Candidate choices for the reviewer (parcels, projects, clusters).
    options: Mapped[list | None] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(16), default="open")
    resolved_by: Mapped[str | None] = mapped_column(String(120))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[dict | None] = mapped_column(JSON)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"))


class AuditLog(Base):
    """Who changed what, when, and what it was before."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer)
    field_name: Mapped[str | None] = mapped_column(String(64))
    old_value: Mapped[dict | None] = mapped_column(JSON)
    new_value: Mapped[dict | None] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text)


class CpraSyncRun(Base):
    """One monthly Inquisitor sync, with its watermark."""

    __tablename__ = "cpra_sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_start: Mapped[date | None] = mapped_column(Date)
    window_end: Mapped[date | None] = mapped_column(Date)
    watermark: Mapped[date | None] = mapped_column(Date)
    campaign_id: Mapped[str | None] = mapped_column(String(64))
    files_seen: Mapped[int] = mapped_column(Integer, default=0)
    files_ingested: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[dict | None] = mapped_column(JSON)


class ApiKey(Base, TimestampMixin):
    """A subscriber API key."""

    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("key_hash", name="uq_api_key_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Only the hash is stored; the key itself is shown once at creation.
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    tier: Mapped[str] = mapped_column(String(16), default="subscriber")
    subject: Mapped[str] = mapped_column(String(160), nullable=False)
    rate_per_minute: Mapped[int | None] = mapped_column(Integer)
    #: Seed for per-subscriber export watermarking.
    watermark_seed: Mapped[str | None] = mapped_column(String(32))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GeneratedMap(Base, TimestampMixin):
    """A rendered parcel map for an application."""

    __tablename__ = "maps"

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("application_clusters.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), default="parcel")
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list | None] = mapped_column(JSON)
    basemap_attribution: Mapped[str | None] = mapped_column(Text)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PageView(Base):
    """One visit to one public page.

    First-party, cookie-free traffic measurement. No IP address is stored:
    ``visitor`` is a hash of address, browser and a salt that changes daily,
    so a visitor can be counted once per day and nothing can be joined across
    days or traced back to a person. Referrers keep only their host.
    """

    __tablename__ = "page_views"
    __table_args__ = (
        Index("ix_page_views_day", "day"),
        Index("ix_page_views_day_path", "day", "path"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    referrer_host: Mapped[str | None] = mapped_column(String(160))
    visitor: Mapped[str] = mapped_column(String(32), nullable=False)
    country: Mapped[str | None] = mapped_column(String(2))
