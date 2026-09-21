"""Provenance: every published fact records where it came from.

Requirement 22 of the build plan is that every important fact knows its
source.  This module gives the whole codebase one shape for that: a
:class:`Provenance` stamp, and a :class:`Sourced` value that carries its stamp
with it from extraction all the way to the public API.

Keeping the stamp attached to the *value* (rather than in a side table written
later) is deliberate -- it makes it impossible to produce a fact without also
producing its source.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from app.core.confidence import Confidence

T = TypeVar("T")


class SourceType:
    """Controlled vocabulary for where a fact came from."""

    PUR_USE_RECORD = "pur_use_record"
    RESTRICTED_MATERIALS_PERMIT = "restricted_materials_permit"
    COUNTY_PERMIT = "county_permit"
    #: Obtained from an agency through a CPRA request run by Inquisitor.
    CPRA_PRODUCTION = "cpra_production"
    DPR_PRODUCT = "dpr_product"
    DPR_LICENSE = "dpr_license"
    EPA_REGISTRATION = "epa_registration"
    PESTICIDE_LABEL = "pesticide_label"
    CALFIRE_GIS = "calfire_gis"
    CALTREES = "caltrees"
    COUNTY_PARCEL_GIS = "county_parcel_gis"
    STATE_PARCEL_GIS = "state_parcel_gis"
    PLSS_GIS = "plss_gis"
    GEOCODER = "geocoder"
    WATCHLIST = "publisher_watchlist"
    ADMIN_REVIEW = "admin_review"
    DERIVED = "derived"


class ExtractionMethod:
    """How the value was pulled out of its source."""

    TABULAR_COLUMN = "tabular_column"
    DOCX_TABLE = "docx_table"
    DOCX_TEXT = "docx_text"
    PDF_TEXT_LAYER = "pdf_text_layer"
    OCR = "ocr"
    REGEX = "regex"
    API = "api"
    SPATIAL_QUERY = "spatial_query"
    COMPUTED = "computed"
    HUMAN = "human"


@dataclass(frozen=True)
class Provenance:
    """Where a single fact came from, and how sure we are of it."""

    source_type: str
    source_name: str
    extraction_method: str
    confidence: str = Confidence.HIGH
    source_id: str | None = None
    source_url: str | None = None
    #: Where inside the source, e.g. ``row 42, column "Product Name"``.
    locator: str | None = None
    #: SHA-256 of the original file, so a fact can be tied to an exact upload.
    source_sha256: str | None = None
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    notes: tuple[str, ...] = ()

    def at(self, locator: str) -> Provenance:
        """Return a copy pointing at a more specific place in the source."""
        return replace(self, locator=locator)

    def with_confidence(self, confidence: str | Confidence) -> Provenance:
        return replace(self, confidence=str(confidence))

    def describe(self) -> str:
        """One-line human-readable citation for the public Sources section."""
        parts = [self.source_name]
        if self.source_id:
            parts.append(f"#{self.source_id}")
        if self.locator:
            parts.append(f"({self.locator})")
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["retrieved_at"] = self.retrieved_at.isoformat()
        data["notes"] = list(self.notes)
        return data


@dataclass(frozen=True)
class Sourced(Generic[T]):
    """A value bound to the provenance that produced it."""

    value: T
    provenance: Provenance

    @property
    def confidence(self) -> Confidence:
        return Confidence(self.provenance.confidence)

    def map(self, fn) -> Sourced:
        """Transform the value, keeping the same provenance."""
        return Sourced(fn(self.value), self.provenance)

    def derive(self, value, *, method: str = ExtractionMethod.COMPUTED, note: str | None = None):
        """Produce a new value derived from this one, inheriting its source.

        Derived facts can never be more certain than what they were derived
        from, so the confidence is carried across unchanged.
        """
        prov = replace(
            self.provenance,
            extraction_method=method,
            notes=self.provenance.notes + ((note,) if note else ()),
        )
        return Sourced(value, prov)

    def to_dict(self) -> dict[str, Any]:
        value = self.value
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        return {"value": value, "source": self.provenance.to_dict()}

    def __bool__(self) -> bool:
        return self.value is not None and self.value != ""


def file_provenance(
    *,
    source_type: str,
    file_name: str,
    sha256: str | None = None,
    source_id: str | None = None,
    method: str = ExtractionMethod.TABULAR_COLUMN,
    confidence: str | Confidence = Confidence.VERIFIED,
) -> Provenance:
    """Build the base stamp for facts read out of an uploaded source file.

    Values read straight off an official document default to ``verified``:
    they are exactly what the record says.  Whether the record is *correct* is
    a separate question, handled by the cross-checks in the pipeline.
    """
    return Provenance(
        source_type=source_type,
        source_name=file_name,
        source_id=source_id,
        source_sha256=sha256,
        extraction_method=method,
        confidence=str(confidence),
    )
