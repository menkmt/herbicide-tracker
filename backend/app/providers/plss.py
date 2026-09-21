"""PLSS section geometry.

A decoded site ID gives a township, range and section; this turns that into a
polygon.  The national source is the BLM's CadNSDI PLSS service, which
publishes first-division sections for the whole country.

Section geometry is cached in the database after the first lookup: sections do
not move, so re-querying a remote service for the same square mile is pure
waste.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.siteid import DecodedSiteId

#: BLM CadNSDI first-division (section) layer.
DEFAULT_PLSS_URL = (
    "https://gis.blm.gov/arcgis/rest/services/Cadastral/BLM_Natl_PLSS_CadNSDI/"
    "MapServer/2"
)


class PlssError(RuntimeError):
    pass


@dataclass
class PlssSection:
    mtrs: str
    geometry: dict[str, Any] | None
    source: str
    source_url: str | None = None


class PlssProvider:
    """Fetches section polygons from an ArcGIS PLSS service."""

    def __init__(self, url: str = DEFAULT_PLSS_URL, *, client: httpx.Client | None = None) -> None:
        self.url = url
        self._client = client or httpx.Client(timeout=45.0)

    def section(self, decoded: DecodedSiteId) -> PlssSection | None:
        """Look up one section by its township, range and section number.

        CadNSDI encodes the township and range as zero-padded strings with a
        direction suffix, and identifies the meridian by its principal meridian
        code, so the decoded values are formatted to match rather than passed
        through raw.
        """
        where = (
            f"TWNSHPNO='{decoded.township:03d}' AND TWNSHPDIR='{decoded.township_dir}' "
            f"AND RANGENO='{decoded.range:03d}' AND RANGEDIR='{decoded.range_dir}' "
            f"AND FRSTDIVNO='{decoded.section}'"
        )
        params = {
            "f": "geojson",
            "where": where,
            "outFields": "FRSTDIVID,TWNSHPNO,RANGENO,FRSTDIVNO",
            "outSR": 4326,
            "returnGeometry": "true",
            "resultRecordCount": 5,
        }
        try:
            response = self._client.get(f"{self.url}/query", params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise PlssError(f"PLSS lookup failed for {decoded.mtrs}: {exc}") from exc
        except ValueError as exc:
            raise PlssError("the PLSS service returned an unreadable response") from exc

        if isinstance(data, dict) and "error" in data:
            raise PlssError(f"PLSS service error: {data['error'].get('message')}")

        features = data.get("features") or []
        if not features:
            return None
        return PlssSection(
            mtrs=decoded.mtrs,
            geometry=features[0].get("geometry"),
            source="BLM CadNSDI PLSS",
            source_url=self.url,
        )


class NullPlssProvider(PlssProvider):
    """Used when no PLSS service is configured or reachable."""

    def __init__(self) -> None:  # noqa: D107 - deliberately does not call super
        self.url = ""

    def section(self, decoded: DecodedSiteId) -> PlssSection | None:
        return None
