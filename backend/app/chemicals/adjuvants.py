"""Identifying the other things that go in the tank.

A forestry spray tank is rarely just herbicide. It routinely contains a
methylated seed oil or a surfactant to make the herbicide stick and spread, a
drift-control polymer, a water conditioner, and very often a blue marker dye
so the applicator can see where they have already sprayed.

These matter to the public for two reasons. People downwind and downhill are
exposed to whatever was actually applied, not only to the active ingredient.
And a blue dye is frequently the thing a member of the public *sees* — on a
stream, on a fence line, on their own property — so a tracker that lists only
"Roundup" cannot answer the question they are actually asking.

They are handled separately from active ingredients because they are a
different kind of thing. Most adjuvants are not EPA-registered pesticides at
all: in California they carry a DPR adjuvant registration instead, so the EPA
registration lookup that resolves a herbicide will not resolve them. They are
classified by name, and they are never counted in active-ingredient totals —
adding a surfactant's gallons to a herbicide's pounds would be meaningless.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.confidence import Confidence


class AdjuvantType:
    """Kinds of non-pesticidal material reported on use reports."""

    SURFACTANT = "surfactant"
    CROP_OIL = "crop_oil"
    DRIFT_CONTROL = "drift_control"
    MARKER_DYE = "marker_dye"
    WATER_CONDITIONER = "water_conditioner"
    DEFOAMER = "defoamer"
    STICKER = "sticker"
    FERTILIZER = "fertilizer"
    OTHER = "other"

    LABELS = {
        SURFACTANT: "Surfactant",
        CROP_OIL: "Crop oil / methylated seed oil",
        DRIFT_CONTROL: "Drift control agent",
        MARKER_DYE: "Spray marker dye",
        WATER_CONDITIONER: "Water conditioner",
        DEFOAMER: "Defoamer",
        STICKER: "Sticker / extender",
        FERTILIZER: "Fertiliser",
        OTHER: "Adjuvant",
    }

    #: Plain-language explanations shown beside the material on a public page.
    DESCRIPTIONS = {
        SURFACTANT: (
            "A wetting agent added so the spray spreads across and sticks to leaves "
            "instead of beading off."
        ),
        CROP_OIL: (
            "An oil-based additive that helps the herbicide penetrate the waxy "
            "surface of leaves."
        ),
        DRIFT_CONTROL: (
            "A thickener added to produce larger droplets, intended to reduce how far "
            "spray drifts from the target area."
        ),
        MARKER_DYE: (
            "A temporary colorant — usually blue — added so the applicator can see "
            "which ground has already been sprayed. It is often what members of the "
            "public notice on vegetation or water after an application."
        ),
        WATER_CONDITIONER: (
            "An additive that adjusts the spray water's hardness or acidity so the "
            "herbicide stays effective."
        ),
        DEFOAMER: "An additive that stops the spray mixture foaming in the tank.",
        STICKER: "An additive that makes the spray adhere to foliage for longer.",
        FERTILIZER: "A nutrient applied with, or in place of, a pesticide.",
        OTHER: "A tank additive that is not itself a pesticide.",
    }

    @classmethod
    def label(cls, value: str) -> str:
        return cls.LABELS.get(value, cls.LABELS[cls.OTHER])

    @classmethod
    def describe(cls, value: str) -> str:
        return cls.DESCRIPTIONS.get(value, cls.DESCRIPTIONS[cls.OTHER])


# Ordered: the first match wins, so specific patterns precede general ones.
_NAME_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(DYE|COLORANT|MARKER|BLAZON|BULLSEYE|HI[- ]?LIGHT|TURF ?MARK|"
                r"SPRAY ?PATTERN INDICATOR|BLUE INDICATOR)\b"), AdjuvantType.MARKER_DYE),
    (re.compile(r"\b(MSO|METHYLATED SEED OIL|CROP OIL|COC|VEGETABLE OIL|"
                r"MODIFIED SEED OIL|SEED OIL)\b"), AdjuvantType.CROP_OIL),
    (re.compile(r"\b(DRIFT|DEPOSITION AID|INVERT|POLYACRYLAMIDE)\b"),
     AdjuvantType.DRIFT_CONTROL),
    (re.compile(r"\b(DEFOAM|ANTIFOAM|ANTI[- ]?FOAM)\b"), AdjuvantType.DEFOAMER),
    (re.compile(r"\b(WATER CONDITIONER|AMS|AMMONIUM SULFATE|BUFFER|ACIDIFIER|"
                r"PH ?ADJUST)\b"), AdjuvantType.WATER_CONDITIONER),
    (re.compile(r"\b(STICKER|EXTENDER|SPREADER[- ]?STICKER)\b"), AdjuvantType.STICKER),
    (re.compile(r"\b(SURFACTANT|NON[- ]?IONIC|NIS|SPREADER|WETTING AGENT|ACTIVATOR|"
                r"INDUCE|R[- ]?11|SPREAD)\b"), AdjuvantType.SURFACTANT),
    (re.compile(r"\b(UREA|NITROGEN|FERTILI[SZ]ER|UAN)\b"), AdjuvantType.FERTILIZER),
)


@dataclass(frozen=True)
class AdjuvantClassification:
    is_adjuvant: bool
    adjuvant_type: str | None
    confidence: str
    reason: str

    @property
    def label(self) -> str | None:
        return AdjuvantType.label(self.adjuvant_type) if self.adjuvant_type else None

    @property
    def description(self) -> str | None:
        return AdjuvantType.describe(self.adjuvant_type) if self.adjuvant_type else None

    def to_dict(self) -> dict:
        return {
            "is_adjuvant": self.is_adjuvant,
            "adjuvant_type": self.adjuvant_type,
            "label": self.label,
            "description": self.description,
            "confidence": self.confidence,
            "reason": self.reason,
        }


NOT_AN_ADJUVANT = AdjuvantClassification(
    is_adjuvant=False,
    adjuvant_type=None,
    confidence=Confidence.LOW,
    reason="not identified as a tank additive",
)


def classify_adjuvant(
    product_name: str | None,
    *,
    known_adjuvant: bool | None = None,
    has_active_ingredients: bool = False,
) -> AdjuvantClassification:
    """Work out whether a reported product is a tank additive, and which kind.

    ``known_adjuvant`` comes from the product registration when it is known and
    outranks the name. A product with identified active ingredients is a
    pesticide, so it is never reclassified as an adjuvant by its name alone.
    """
    name = (product_name or "").upper()

    if has_active_ingredients and not known_adjuvant:
        return NOT_AN_ADJUVANT

    matched_type: str | None = None
    for pattern, adjuvant_type in _NAME_PATTERNS:
        if pattern.search(name):
            matched_type = adjuvant_type
            break

    if known_adjuvant:
        return AdjuvantClassification(
            is_adjuvant=True,
            adjuvant_type=matched_type or AdjuvantType.OTHER,
            confidence=Confidence.VERIFIED if matched_type else Confidence.HIGH,
            reason=(
                f"registered as an adjuvant; identified as a "
                f"{AdjuvantType.label(matched_type or AdjuvantType.OTHER).lower()} from its name"
                if matched_type
                else "registered as an adjuvant"
            ),
        )

    if matched_type:
        return AdjuvantClassification(
            is_adjuvant=True,
            adjuvant_type=matched_type,
            confidence=Confidence.MEDIUM,
            reason=(
                f"product name {product_name!r} indicates a "
                f"{AdjuvantType.label(matched_type).lower()}; confirm against the "
                "adjuvant registration before relying on it"
            ),
        )

    return NOT_AN_ADJUVANT
