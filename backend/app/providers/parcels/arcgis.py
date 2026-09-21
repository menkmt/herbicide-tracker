"""A parcel provider for any county publishing an ArcGIS REST parcel layer.

Most California counties expose their assessor parcels through an ArcGIS
FeatureServer or MapServer.  The service URL and field names differ from
county to county, but the query protocol does not, so one provider plus a
per-county field mapping covers all of them.

Adding a county is therefore configuration — a URL and a handful of field
names — rather than new code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.providers.parcels.base import ParcelProvider, ParcelProviderError, ParcelRecord

#: ArcGIS caps a single response; anything larger must be paged.
DEFAULT_PAGE_SIZE = 500

#: Guard against a mis-scoped query pulling an entire county.
MAX_FEATURES = 5000


@dataclass(frozen=True)
class ArcGisFieldMap:
    """Which attributes in a county's layer hold which facts."""

    apn: str = "APN"
    owner: str = "OWNER"
    acreage: str | None = "ACRES"
    address: str | None = "SITUS_ADDR"
    use_code: str | None = None


@dataclass(frozen=True)
class ArcGisLayerConfig:
    """Everything needed to query one county's parcel layer."""

    name: str
    url: str
    fields: ArcGisFieldMap = ArcGisFieldMap()
    #: Layers sometimes hold data in a projected CRS; we always ask for 4326.
    out_srid: int = 4326
    timeout: float = 45.0


class ArcGisParcelProvider(ParcelProvider):
    """Queries an ArcGIS REST parcel layer."""

    def __init__(self, config: ArcGisLayerConfig, *, client: httpx.Client | None = None) -> None:
        self.config = config
        self.name = config.name
        self._client = client or httpx.Client(timeout=config.timeout)

    # -- plumbing ---------------------------------------------------------
    def _query(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Run a paged ArcGIS query and return every feature."""
        url = f"{self.config.url.rstrip('/')}/query"
        collected: list[dict[str, Any]] = []
        offset = 0

        while True:
            payload = {
                "f": "geojson",
                "outFields": "*",
                "outSR": self.config.out_srid,
                "returnGeometry": "true",
                "resultRecordCount": DEFAULT_PAGE_SIZE,
                "resultOffset": offset,
                **params,
            }
            try:
                response = self._client.get(url, params=payload)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as exc:
                raise ParcelProviderError(f"{self.name}: request failed: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise ParcelProviderError(
                    f"{self.name}: the service returned a non-JSON response"
                ) from exc

            # ArcGIS reports errors with HTTP 200 and an error object.
            if isinstance(data, dict) and "error" in data:
                message = data["error"].get("message", "unknown error")
                raise ParcelProviderError(f"{self.name}: {message}")

            features = data.get("features") or []
            collected.extend(features)

            if len(features) < DEFAULT_PAGE_SIZE or len(collected) >= MAX_FEATURES:
                break
            offset += DEFAULT_PAGE_SIZE

        return collected[:MAX_FEATURES]

    def _to_record(self, feature: dict[str, Any]) -> ParcelRecord | None:
        properties = feature.get("properties") or feature.get("attributes") or {}
        fields = self.config.fields
        apn = properties.get(fields.apn)
        if not apn:
            return None

        def optional(name: str | None) -> Any:
            return properties.get(name) if name else None

        acreage = optional(fields.acreage)
        try:
            acreage = float(acreage) if acreage is not None else None
        except (TypeError, ValueError):
            acreage = None

        return ParcelRecord(
            apn=str(apn).strip(),
            owner=(str(optional(fields.owner)).strip() if optional(fields.owner) else None),
            geometry=feature.get("geometry"),
            acreage=acreage,
            address=(str(optional(fields.address)).strip() if optional(fields.address) else None),
            use_code=(str(optional(fields.use_code)) if optional(fields.use_code) else None),
            source=self.name,
            source_url=self.config.url,
        )

    # -- interface --------------------------------------------------------
    def parcels_in_geometry(self, geometry: dict[str, Any]) -> list[ParcelRecord]:
        """Parcels intersecting a GeoJSON polygon.

        ArcGIS takes its own geometry format rather than GeoJSON for the input
        filter, so the ring coordinates are translated; the *response* is
        requested as GeoJSON, which every modern service supports.
        """
        rings = _geojson_to_rings(geometry)
        if not rings:
            return []
        features = self._query(
            {
                "geometry": json.dumps({"rings": rings, "spatialReference": {"wkid": 4326}}),
                "geometryType": "esriGeometryPolygon",
                "inSR": 4326,
                "spatialRel": "esriSpatialRelIntersects",
                "where": "1=1",
            }
        )
        return [r for r in (self._to_record(f) for f in features) if r]

    def parcels_by_owner(self, owner: str) -> list[ParcelRecord]:
        """Parcels whose owner field contains this text.

        The match here is deliberately loose — a substring search on the
        distinctive part of the name. Deciding whether a returned parcel really
        belongs to the operator is done afterwards by the normalised name
        comparison, which understands that "WM BEATY AND ASSOC." and "W.M.
        Beaty & Associates, Inc." are the same business.
        """
        token = _sql_escape(_distinctive_token(owner))
        if not token:
            return []
        features = self._query(
            {"where": f"UPPER({self.config.fields.owner}) LIKE '%{token}%'"}
        )
        return [r for r in (self._to_record(f) for f in features) if r]

    def parcel_by_apn(self, apn: str) -> ParcelRecord | None:
        features = self._query(
            {"where": f"{self.config.fields.apn} = '{_sql_escape(apn)}'"}
        )
        records = [r for r in (self._to_record(f) for f in features) if r]
        return records[0] if records else None


def _sql_escape(value: str) -> str:
    """Escape a value for an ArcGIS ``where`` clause.

    ArcGIS where-clauses are SQL, so a name containing an apostrophe — which
    is common in business names — must be escaped or the query breaks and, in
    the worst case, becomes injectable.
    """
    return str(value).replace("'", "''").replace("\\", "")


def _distinctive_token(owner: str) -> str:
    """The most identifying word of an owner name, for a LIKE search."""
    from app.core.normalize import normalize_company

    normalized = normalize_company(owner)
    tokens = [t for t in normalized.core_tokens if len(t) > 2]
    if not tokens:
        tokens = [t for t in normalized.tokens if len(t) > 2]
    if not tokens:
        return ""
    # The longest distinctive word is the most selective.
    return max(tokens, key=len)


def _geojson_to_rings(geometry: dict[str, Any]) -> list[list[list[float]]]:
    """Convert GeoJSON Polygon/MultiPolygon coordinates to ArcGIS rings."""
    if not geometry:
        return []
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if kind == "Polygon":
        return [[list(point) for point in ring] for ring in coordinates]
    if kind == "MultiPolygon":
        rings: list[list[list[float]]] = []
        for polygon in coordinates:
            rings.extend([list(point) for point in ring] for ring in polygon)
        return rings
    return []
