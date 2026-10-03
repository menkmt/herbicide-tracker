"""Who manages the land in a section: national forest, BLM, state or private.

Answers the popup question "is this national forest or private land?" from
the BLM's national Surface Management Agency layer, which maps every federal
and state land manager in the country; anything it does not cover is
private. One point query per section, at its centroid, cached with the
section. Sections that straddle a boundary take the manager at the centre,
and the label says "at the section's centre" so it is not overclaimed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

#: BLM Surface Management Agency polygons (federal and state land managers).
DEFAULT_SMA_URL = (
    "https://gis.blm.gov/arcgis/rest/services/lands/BLM_Natl_SMA_LimitedScale/MapServer/1"
)

#: Field names the SMA layer has used for the managing agency and unit.
AGENCY_FIELDS = ("ADMIN_AGENCY_CODE", "ADMIN_AGENCY", "AGENCY_CODE", "AGENCY", "SMA_CODE")
UNIT_FIELDS = ("ADMIN_UNIT_NAME", "ADMIN_UNIT", "UNIT_NAME", "ADMIN_DEPT_CODE")

#: Agency codes onto the labels a reader understands.
AGENCY_LABELS = {
    "USFS": ("national_forest", "National forest"),
    "FS": ("national_forest", "National forest"),
    "BLM": ("blm", "Bureau of Land Management"),
    "NPS": ("federal", "National Park Service"),
    "FWS": ("federal", "U.S. Fish & Wildlife Service"),
    "BOR": ("federal", "Bureau of Reclamation"),
    "DOD": ("federal", "Department of Defense"),
    "BIA": ("tribal", "Tribal land"),
    "ST": ("state", "State land"),
    "STATE": ("state", "State land"),
    "LG": ("local", "Local government"),
    "PVT": ("private", "Private land"),
}


class LandManagerError(RuntimeError):
    pass


@dataclass
class LandManager:
    #: national_forest | blm | federal | state | local | tribal | private
    category: str
    label: str
    unit: str | None
    source: str


class LandManagerProvider:
    def __init__(self, url: str = DEFAULT_SMA_URL, *, client: httpx.Client | None = None) -> None:
        self.url = url
        self._client = client or httpx.Client(timeout=30.0)

    def at(self, lon: float, lat: float) -> LandManager:
        params = {
            "f": "json",
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "false",
        }
        try:
            response = self._client.get(f"{self.url}/query", params=params)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LandManagerError(f"land manager lookup failed: {exc}") from exc
        if isinstance(data, dict) and "error" in data:
            raise LandManagerError(f"land manager service error: {data['error'].get('message')}")
        return classify((data.get("features") or [{}])[0].get("attributes") or {})


def classify(attributes: dict[str, Any]) -> LandManager:
    """Turn one SMA feature's attributes into a label. No feature means the
    land has no federal or state manager, which is to say it is private."""
    source = "BLM Surface Management Agency"
    if not attributes:
        return LandManager("private", "Private land", None, source)
    code = next(
        (str(attributes[f]).strip().upper() for f in AGENCY_FIELDS if attributes.get(f)), ""
    )
    unit = next((str(attributes[f]).strip() for f in UNIT_FIELDS if attributes.get(f)), None)
    category, label = AGENCY_LABELS.get(code, ("federal" if code else "private",
                                              code.title() if code else "Private land"))
    if category == "national_forest" and unit and "forest" not in unit.lower():
        unit = f"{unit} National Forest"
    return LandManager(category, label, unit, source)
