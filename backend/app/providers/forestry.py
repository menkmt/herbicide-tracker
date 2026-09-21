"""Finding the official forestry project behind an application.

Forestry herbicide work happens under a CAL FIRE forest-practice document — a
timber harvesting plan, an exemption, an emergency notice, an NTMP.  Those
documents have real project names, and the whole point of this resolver is to
find the *actual* name rather than invent a descriptive one.

The chain is:

    PUR sections -> cluster geometry -> CAL FIRE Forest Practice GIS
                 -> overlapping plan -> plan identifier -> CalTREES metadata

Two rules govern the result:

* **Never invent a name.**  If no plan overlaps, or several do and none is
  clearly the right one, the application keeps the property owner as its title
  and the ambiguity goes to review.
* **Overlap is not proof.**  A timber harvesting plan covering the same
  section as a herbicide application is strong evidence they are the same
  project, but it is evidence, not identity — so the association is recorded
  with its confidence and the plan it came from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

from app.core.confidence import Confidence
from app.core.normalize import compare_companies

#: CAL FIRE's Forest Practice programme map service.
DEFAULT_FOREST_PRACTICE_URL = (
    "https://egis.fire.ca.gov/arcgis/rest/services/FRAP/ForestPractice/MapServer"
)

#: Plan kinds the resolver recognises, in the order they are preferred when
#: several overlap: a specific harvest document beats a long-lived management
#: plan covering the same ground.
PLAN_KIND_PRIORITY = (
    "THP",
    "Emergency Notice",
    "Exemption",
    "Notice of Timber Operations",
    "NTMP",
    "Working Forest Management Plan",
)


class ForestryError(RuntimeError):
    pass


@dataclass
class ForestryPlan:
    """A CAL FIRE forest-practice document overlapping an application."""

    identifier: str
    name: str | None = None
    kind: str | None = None
    landowner: str | None = None
    county: str | None = None
    filed_on: date | None = None
    status: str | None = None
    geometry: dict[str, Any] | None = None
    source_url: str | None = None
    #: Fraction of the application's area covered by this plan, 0-1.
    overlap_ratio: float | None = None

    @property
    def caltrees_url(self) -> str | None:
        if not self.identifier:
            return None
        return f"https://caltreesplans.resources.ca.gov/Caltrees/Report/ShowReport.aspx?module=TH_Document&reportID={self.identifier}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "name": self.name,
            "kind": self.kind,
            "landowner": self.landowner,
            "county": self.county,
            "filed_on": self.filed_on.isoformat() if self.filed_on else None,
            "status": self.status,
            "overlap_ratio": self.overlap_ratio,
            "source_url": self.source_url,
            "caltrees_url": self.caltrees_url,
        }


@dataclass
class ProjectNameResult:
    """The resolver's answer about an application's project name."""

    name: str | None = None
    identifier: str | None = None
    plan: ForestryPlan | None = None
    confidence: str = Confidence.LOW
    basis: str = ""
    alternatives: list[ForestryPlan] = field(default_factory=list)
    needs_review: bool = False
    review_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "identifier": self.identifier,
            "plan": self.plan.to_dict() if self.plan else None,
            "confidence": self.confidence,
            "basis": self.basis,
            "alternatives": [p.to_dict() for p in self.alternatives],
            "needs_review": self.needs_review,
            "review_reason": self.review_reason,
        }


class ForestPracticeProvider:
    """Queries CAL FIRE's Forest Practice GIS for overlapping plans."""

    def __init__(
        self,
        url: str = DEFAULT_FOREST_PRACTICE_URL,
        *,
        layers: tuple[int, ...] = (0, 1, 2, 3),
        client: httpx.Client | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.layers = layers
        self._client = client or httpx.Client(timeout=60.0)

    def plans_intersecting(self, geometry: dict[str, Any]) -> list[ForestryPlan]:
        """Every forest-practice document overlapping a geometry."""
        from app.providers.parcels.arcgis import _geojson_to_rings

        rings = _geojson_to_rings(geometry)
        if not rings:
            return []

        plans: list[ForestryPlan] = []
        for layer in self.layers:
            params = {
                "f": "geojson",
                "geometry": json.dumps({"rings": rings, "spatialReference": {"wkid": 4326}}),
                "geometryType": "esriGeometryPolygon",
                "inSR": 4326,
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "*",
                "outSR": 4326,
                "returnGeometry": "true",
                "where": "1=1",
            }
            try:
                response = self._client.get(f"{self.url}/{layer}/query", params=params)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as exc:
                raise ForestryError(f"CAL FIRE query failed: {exc}") from exc
            except ValueError as exc:
                raise ForestryError("CAL FIRE returned an unreadable response") from exc

            if isinstance(data, dict) and "error" in data:
                continue

            for feature in data.get("features") or []:
                plan = _feature_to_plan(feature, f"{self.url}/{layer}")
                if plan is not None:
                    plans.append(plan)
        return plans


#: Attribute names CAL FIRE uses for the same facts across its layers.
_IDENTIFIER_FIELDS = ("THP_NUM", "PLAN_NO", "DOCUMENT_N", "HARVEST_DO", "NOTIF_NUM", "ID")
_NAME_FIELDS = ("PLAN_NAME", "THP_NAME", "NAME", "PROJECT_NA", "DOCUMENT_T")
_OWNER_FIELDS = ("LANDOWNER", "OWNER", "TIMBER_OWN", "LANDOWNER_")
_KIND_FIELDS = ("DOC_TYPE", "TYPE", "PLAN_TYPE", "DOCUMENT_T")
_COUNTY_FIELDS = ("COUNTY", "COUNTY_NAM")
_STATUS_FIELDS = ("STATUS", "PLAN_STATU")


def _first_field(properties: dict[str, Any], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = properties.get(name)
        if value not in (None, "", " "):
            return str(value).strip()
    return None


def _feature_to_plan(feature: dict[str, Any], source_url: str) -> ForestryPlan | None:
    properties = feature.get("properties") or feature.get("attributes") or {}
    identifier = _first_field(properties, _IDENTIFIER_FIELDS)
    if not identifier:
        return None
    return ForestryPlan(
        identifier=identifier,
        name=_first_field(properties, _NAME_FIELDS),
        kind=_first_field(properties, _KIND_FIELDS),
        landowner=_first_field(properties, _OWNER_FIELDS),
        county=_first_field(properties, _COUNTY_FIELDS),
        status=_first_field(properties, _STATUS_FIELDS),
        geometry=feature.get("geometry"),
        source_url=source_url,
    )


def choose_project_name(
    plans: list[ForestryPlan],
    *,
    owner: str | None,
    application_dates: tuple[date | None, date | None] = (None, None),
) -> ProjectNameResult:
    """Pick the forestry project an application belongs to, or decline to.

    Preference order, strongest evidence first:

    1. A single overlapping plan whose landowner matches the operator.
    2. Several such plans — the most specific harvest document wins, but the
       choice goes to review because two real projects may overlap.
    3. Overlapping plans with a different landowner, or none at all — no name
       is claimed.
    """
    if not plans:
        return ProjectNameResult(
            basis="no CAL FIRE forest-practice document overlaps this application",
            confidence=Confidence.LOW,
        )

    named = [p for p in plans if p.name]
    if owner:
        owner_matches = [
            p for p in named if p.landowner and compare_companies(owner, p.landowner).matched
        ]
    else:
        owner_matches = []

    if len(owner_matches) == 1:
        plan = owner_matches[0]
        return ProjectNameResult(
            name=plan.name,
            identifier=plan.identifier,
            plan=plan,
            confidence=Confidence.HIGH,
            basis=(
                f"overlapping CAL FIRE {plan.kind or 'forest practice document'} "
                f"{plan.identifier}, filed by the same landowner"
            ),
        )

    if len(owner_matches) > 1:
        ordered = sorted(owner_matches, key=_kind_rank)
        return ProjectNameResult(
            name=None,
            identifier=None,
            plan=None,
            confidence=Confidence.MEDIUM,
            basis=(
                f"{len(owner_matches)} CAL FIRE documents filed by this landowner overlap "
                "this application"
            ),
            alternatives=ordered,
            needs_review=True,
            review_reason="multiple_possible_projects",
        )

    if named:
        return ProjectNameResult(
            confidence=Confidence.LOW,
            basis=(
                f"{len(named)} forest-practice document(s) overlap this application but none "
                "is filed by the operator named on the use reports"
            ),
            alternatives=sorted(named, key=_kind_rank)[:10],
            needs_review=True,
            review_reason="project_owner_mismatch",
        )

    return ProjectNameResult(
        confidence=Confidence.LOW,
        basis="overlapping forest-practice documents carry no project name",
    )


def _kind_rank(plan: ForestryPlan) -> int:
    kind = (plan.kind or "").upper()
    for index, candidate in enumerate(PLAN_KIND_PRIORITY):
        if candidate.upper() in kind:
            return index
    return len(PLAN_KIND_PRIORITY)
