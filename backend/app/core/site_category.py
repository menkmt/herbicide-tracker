"""Classifying what kind of site an application treated.

this tracker is about **forestry** herbicide use.  California PURs
cover every pesticide application in the state, the overwhelming majority of
which is agricultural and out of scope.  Filtering is therefore not cosmetic —
it decides what the tracker is.

There *is* a reliable way to do it.  Every PUR carries DPR's commodity/site
code and name, and forestry has its own: ``30000`` — ``FOREST, TMBRLND``.  Every
row of the real Lassen data carries exactly that.  So the primary filter is the
code, which is authoritative and stable.

The code alone is not quite enough, because counties truncate it, OCR mangles
it, and some exports supply only the name.  So classification falls back to the
site *name*, and anything that matches neither is marked ``UNKNOWN`` and sent to
review rather than being guessed into or out of scope.

Scope is staged deliberately.  V1 publishes forestry only.  Rights-of-way
(roadside), invasive-plant and aquatic/waterway treatments are recognised and
classified from day one — so the records are already captured, categorised and
queryable — but are not published until the forestry side is complete.  Turning
them on is a change to :data:`PUBLISHED_CATEGORIES`, not a re-import.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.confidence import Confidence


class SiteCategory:
    """Kinds of treatment site the tracker distinguishes."""

    FORESTRY = "forestry"
    RIGHTS_OF_WAY = "rights_of_way"
    AQUATIC = "aquatic"
    INVASIVE_PLANT = "invasive_plant"
    AGRICULTURE = "agriculture"
    LANDSCAPE = "landscape"
    STRUCTURAL = "structural"
    OTHER = "other"
    UNKNOWN = "unknown"

    LABELS = {
        FORESTRY: "Forestry / timberland",
        RIGHTS_OF_WAY: "Rights of way / roadside",
        AQUATIC: "Waterway / water body",
        INVASIVE_PLANT: "Invasive plant control",
        AGRICULTURE: "Agriculture",
        LANDSCAPE: "Landscape / ornamental",
        STRUCTURAL: "Structural",
        OTHER: "Other",
        UNKNOWN: "Unclassified",
    }

    @classmethod
    def label(cls, category: str) -> str:
        return cls.LABELS.get(category, category)


#: Categories published on the public site right now.  Forestry only for V1.
PUBLISHED_CATEGORIES: frozenset[str] = frozenset({SiteCategory.FORESTRY})

#: Categories the tracker classifies and stores but does not yet publish.
#: These come online once the forestry side is complete; no re-import needed.
PLANNED_CATEGORIES: frozenset[str] = frozenset(
    {SiteCategory.RIGHTS_OF_WAY, SiteCategory.INVASIVE_PLANT, SiteCategory.AQUATIC}
)

#: DPR commodity/site codes, keyed by the leading digits of the code.
#: ``30000-0`` and ``30000`` both resolve through the five-digit prefix.
#: Confirmed against real Lassen County use records and permits.
SITE_CODE_CATEGORIES: dict[str, str] = {
    "30000": SiteCategory.FORESTRY,  # FOREST, TMBRLND
}

# Name patterns, applied when the code is missing, truncated or unrecognised.
# Ordered: the first match wins, so the more specific patterns come first.
_NAME_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(FOREST|TIMBER|TMBRLND|TIMBERLAND|SILVICULT|CHRISTMAS TREE)\b"),
     SiteCategory.FORESTRY),
    (re.compile(r"\b(RIGHT[S]? OF WAY|RIGHTOFWAY|ROADSIDE|ROADWAY|HIGHWAY|RAILROAD|"
                r"UTILITY|POWERLINE|PIPELINE)\b"), SiteCategory.RIGHTS_OF_WAY),
    (re.compile(r"\b(AQUATIC|WATER AREA|WATERWAY|WATER BODY|DITCH|CANAL|RESERVOIR|"
                r"IRRIGATION SYSTEM|DRAINAGE|RIPARIAN|WETLAND)\b"), SiteCategory.AQUATIC),
    (re.compile(r"\b(INVASIVE|NOXIOUS WEED|WEED ABATEMENT|VEGETATION MANAGEMENT)\b"),
     SiteCategory.INVASIVE_PLANT),
    (re.compile(r"\b(LANDSCAPE|ORNAMENTAL|TURF|GOLF|NURSERY|GREENHOUSE|PARK)\b"),
     SiteCategory.LANDSCAPE),
    (re.compile(r"\b(STRUCTUR|BUILDING|WAREHOUSE|RESIDENTIAL)\b"), SiteCategory.STRUCTURAL),
    (re.compile(r"\b(UNCULTIVATED|NON[- ]?AG|NONCROP|NON[- ]?CROP|FALLOW|RANGELAND|"
                r"PASTURE)\b"), SiteCategory.OTHER),
)


@dataclass(frozen=True)
class SiteClassification:
    """How a record's treatment site was categorised, and on what basis."""

    category: str
    confidence: str
    reason: str
    matched_code: str | None = None

    @property
    def label(self) -> str:
        return SiteCategory.label(self.category)

    @property
    def is_published(self) -> bool:
        """Whether this category is in the tracker's current publishing scope."""
        return self.category in PUBLISHED_CATEGORIES

    @property
    def is_planned(self) -> bool:
        return self.category in PLANNED_CATEGORIES

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "label": self.label,
            "confidence": self.confidence,
            "reason": self.reason,
            "matched_code": self.matched_code,
            "is_published": self.is_published,
            "is_planned": self.is_planned,
        }


def _normalise_code(code: str | None) -> str | None:
    """Reduce ``30000-0`` / ``30000 `` to the five-digit site code."""
    if not code:
        return None
    digits = re.sub(r"[^0-9]", "", str(code).split("-")[0])
    if not digits:
        return None
    return digits[:5].zfill(5) if len(digits) <= 5 else digits[:5]


def classify_site(
    commodity_code: str | None = None,
    commodity_name: str | None = None,
) -> SiteClassification:
    """Classify a treatment site from its DPR commodity code and/or name.

    The code is authoritative, so a code match is ``verified``.  A name match
    is a strong but linguistic inference, so it is ``high``.  Anything else is
    ``unknown`` at ``low`` confidence and belongs in review — never assumed to
    be out of scope, because a misclassified forestry application is a record
    the public silently never sees.
    """
    code = _normalise_code(commodity_code)
    if code and code in SITE_CODE_CATEGORIES:
        category = SITE_CODE_CATEGORIES[code]
        return SiteClassification(
            category=category,
            confidence=Confidence.VERIFIED,
            reason=f"DPR site code {code} is {SiteCategory.label(category).lower()}",
            matched_code=code,
        )

    if commodity_name:
        name = str(commodity_name).upper()
        for pattern, category in _NAME_PATTERNS:
            if pattern.search(name):
                return SiteClassification(
                    category=category,
                    confidence=Confidence.HIGH,
                    reason=f"site name {commodity_name!r} matches {SiteCategory.label(category)}",
                    matched_code=code,
                )
        # A named site that matches nothing above is most likely a crop.
        return SiteClassification(
            category=SiteCategory.UNKNOWN,
            confidence=Confidence.LOW,
            reason=(
                f"site {commodity_name!r}"
                + (f" (code {code})" if code else "")
                + " did not match any known site category; it may be an agricultural "
                "crop, but it is held for review rather than assumed out of scope"
            ),
            matched_code=code,
        )

    return SiteClassification(
        category=SiteCategory.UNKNOWN,
        confidence=Confidence.LOW,
        reason="no commodity code or site name was reported",
        matched_code=code,
    )


def is_in_publishing_scope(classification: SiteClassification) -> bool:
    return classification.is_published
