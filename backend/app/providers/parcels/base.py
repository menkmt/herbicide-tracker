"""Parcel providers: one interface, many county data sources.

California has no single statewide parcel service, so the tracker treats
parcel lookup as a pluggable per-county concern.  Each provider returns the
same normalised record whatever its source looks like, which is what lets the
rest of the pipeline stay county-agnostic.

The parcel layer answers one question: *which properties are associated with
this application?*  It deliberately does not claim those properties were
sprayed — see :mod:`app.pipeline.parcels_stage`, where the association is
recorded with the basis on which it was made.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.normalize import company_key


@dataclass
class ParcelRecord:
    """A parcel, normalised across providers."""

    apn: str
    owner: str | None = None
    #: GeoJSON geometry in WGS84.
    geometry: dict[str, Any] | None = None
    acreage: float | None = None
    address: str | None = None
    use_code: str | None = None
    source: str = ""
    source_url: str | None = None
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def owner_key(self) -> str:
        return company_key(self.owner)

    def to_dict(self) -> dict[str, Any]:
        return {
            "apn": self.apn,
            "owner": self.owner,
            "geometry": self.geometry,
            "acreage": self.acreage,
            "address": self.address,
            "use_code": self.use_code,
            "source": self.source,
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at.isoformat(),
        }


class ParcelProviderError(RuntimeError):
    pass


class ParcelProvider(ABC):
    """Fetches parcels for a county."""

    #: Human-readable provider name, recorded as the source of every parcel.
    name: str = "parcel provider"

    @abstractmethod
    def parcels_in_geometry(self, geometry: dict[str, Any]) -> list[ParcelRecord]:
        """Parcels intersecting a GeoJSON geometry (usually a PLSS section)."""

    @abstractmethod
    def parcels_by_owner(self, owner: str) -> list[ParcelRecord]:
        """Parcels whose recorded owner resembles this name."""

    def parcel_by_apn(self, apn: str) -> ParcelRecord | None:
        """Optional: a single parcel by assessor parcel number."""
        return None


class NullParcelProvider(ParcelProvider):
    """Used for counties with no configured parcel source.

    Returning nothing is the honest answer — better than a wrong parcel. The
    pipeline records "no parcel source configured for this county" as a review
    reason so the gap is visible rather than looking like an absence of data.
    """

    name = "no parcel source configured"

    def parcels_in_geometry(self, geometry: dict[str, Any]) -> list[ParcelRecord]:
        return []

    def parcels_by_owner(self, owner: str) -> list[ParcelRecord]:
        return []
