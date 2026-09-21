"""Quantity units, and why they cannot simply be added together.

A single application reports quantities in whatever unit the label uses:
gallons of Roundup, ounces of Transline, pounds of Velpar DF.  The real Lassen
data uses all three.  Adding those numbers together produces a figure that
means nothing, so this module keeps them apart until there is a defensible way
to combine them.

Three rules:

1. **Units are grouped into families** (volume, mass, count).  Totals are
   accumulated per family and only ever summed within one.
2. **"Ounce" is ambiguous.**  It is a fluid ounce for a liquid and a weight
   ounce for a dry formulation.  The formulation decides, and when the
   formulation is unknown the quantity is held in an ``unknown`` bucket and
   reported separately rather than guessed.
3. **Pounds of active ingredient is the real measure.**  It is the standard
   unit for pesticide-use reporting and the only one comparable across
   products.  It is computed when the active-ingredient percentage — and, for
   liquids, the formulation density — are known, and left ``None`` with a
   stated reason when they are not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


class UnitFamily:
    VOLUME = "volume"
    MASS = "mass"
    COUNT = "count"
    UNKNOWN = "unknown"


#: Liquid volume units expressed in US gallons.
VOLUME_TO_GALLONS: dict[str, float] = {
    "GALLON": 1.0,
    "GALLONS": 1.0,
    "GAL": 1.0,
    "QUART": 0.25,
    "QT": 0.25,
    "PINT": 0.125,
    "PT": 0.125,
    "FLUID OUNCE": 1 / 128,
    "FL OZ": 1 / 128,
    "FLOZ": 1 / 128,
    "LITER": 0.264172,
    "LITRE": 0.264172,
    "L": 0.264172,
    "MILLILITER": 0.000264172,
    "ML": 0.000264172,
}

#: Mass units expressed in pounds.
MASS_TO_POUNDS: dict[str, float] = {
    "POUND": 1.0,
    "POUNDS": 1.0,
    "LB": 1.0,
    "LBS": 1.0,
    "TON": 2000.0,
    "TONS": 2000.0,
    "OUNCE (DRY)": 1 / 16,
    "DRY OUNCE": 1 / 16,
    "OZ (DRY)": 1 / 16,
    "GRAM": 0.00220462,
    "G": 0.00220462,
    "KILOGRAM": 2.20462,
    "KG": 2.20462,
}

COUNT_UNITS = {"EACH", "UNIT", "UNITS", "CONTAINER", "CONTAINERS", "PIECE"}

#: Units that could be either family until the formulation is known.
AMBIGUOUS_UNITS = {"OUNCE", "OUNCES", "OZ"}

#: Formulation codes and words indicating a dry product, for which an "ounce"
#: is a weight ounce.  DF = dry flowable, WDG/WG = water-dispersible granule,
#: SG = soluble granule, WP = wettable powder.
DRY_FORMULATION_TOKENS = (
    "DF", "WDG", "WG", "SG", "WP", "DG", "G", "GR",
    "DRY", "GRANULE", "GRANULAR", "POWDER", "PELLET", "BAIT",
)

#: Formulation codes indicating a liquid, for which an "ounce" is fluid.
LIQUID_FORMULATION_TOKENS = (
    "SL", "EC", "SC", "ME", "EW", "SE", "AS", "LC", "L",
    "LIQUID", "SOLUTION", "CONCENTRATE", "EMULSIFIABLE",
)


def normalize_unit(unit: str | None) -> str:
    if not unit:
        return ""
    return re.sub(r"[^A-Z ]", " ", str(unit).upper()).strip()


def infer_formulation_state(product_name: str | None, formulation: str | None) -> str | None:
    """Guess whether a product is liquid or dry from its name or formulation code.

    ``"DU PONT VELPAR DF HERBICIDE"`` is dry (DF = dry flowable);
    ``"ALLIGARE ROTARY 2 SL"`` is liquid (SL = soluble liquid).  Returns
    ``"liquid"``, ``"dry"`` or ``None`` when neither is indicated.
    """
    haystacks = [h for h in (formulation, product_name) if h]
    for haystack in haystacks:
        tokens = re.split(r"[^A-Z0-9]+", str(haystack).upper())
        for token in tokens:
            if token in DRY_FORMULATION_TOKENS:
                return "dry"
        for token in tokens:
            if token in LIQUID_FORMULATION_TOKENS:
                return "liquid"
    return None


@dataclass(frozen=True)
class NormalizedQuantity:
    """A reported quantity resolved onto a canonical unit where possible."""

    family: str
    gallons: float | None = None
    pounds: float | None = None
    count: float | None = None
    original_amount: float | None = None
    original_unit: str | None = None
    note: str | None = None

    @property
    def is_resolved(self) -> bool:
        return self.family != UnitFamily.UNKNOWN


def normalize_quantity(
    amount: float | None,
    unit: str | None,
    *,
    product_name: str | None = None,
    formulation: str | None = None,
) -> NormalizedQuantity:
    """Convert a reported quantity to gallons or pounds where it is safe to.

    An ambiguous "ounce" is resolved using the product's formulation; when that
    is unknown the quantity stays in the ``unknown`` family, carrying a note
    explaining why, so totals can report it separately instead of absorbing it
    into a wrong number.
    """
    if amount is None:
        return NormalizedQuantity(UnitFamily.UNKNOWN, note="no quantity reported")

    key = normalize_unit(unit)
    if not key:
        return NormalizedQuantity(
            UnitFamily.UNKNOWN,
            original_amount=amount,
            original_unit=unit,
            note="no unit reported",
        )

    if key in AMBIGUOUS_UNITS:
        state = infer_formulation_state(product_name, formulation)
        if state == "dry":
            return NormalizedQuantity(
                UnitFamily.MASS,
                pounds=amount / 16,
                original_amount=amount,
                original_unit=unit,
                note="'ounce' read as a weight ounce because the product is a dry formulation",
            )
        if state == "liquid":
            return NormalizedQuantity(
                UnitFamily.VOLUME,
                gallons=amount / 128,
                original_amount=amount,
                original_unit=unit,
                note="'ounce' read as a fluid ounce because the product is a liquid",
            )
        return NormalizedQuantity(
            UnitFamily.UNKNOWN,
            original_amount=amount,
            original_unit=unit,
            note=(
                "'ounce' is ambiguous without knowing whether the product is liquid or "
                "dry; reported separately rather than assumed"
            ),
        )

    if key in VOLUME_TO_GALLONS:
        return NormalizedQuantity(
            UnitFamily.VOLUME,
            gallons=amount * VOLUME_TO_GALLONS[key],
            original_amount=amount,
            original_unit=unit,
        )
    if key in MASS_TO_POUNDS:
        return NormalizedQuantity(
            UnitFamily.MASS,
            pounds=amount * MASS_TO_POUNDS[key],
            original_amount=amount,
            original_unit=unit,
        )
    if key in COUNT_UNITS:
        return NormalizedQuantity(
            UnitFamily.COUNT,
            count=amount,
            original_amount=amount,
            original_unit=unit,
        )

    return NormalizedQuantity(
        UnitFamily.UNKNOWN,
        original_amount=amount,
        original_unit=unit,
        note=f"unit {unit!r} is not recognised",
    )


def active_ingredient_pounds(
    quantity: NormalizedQuantity,
    *,
    ingredient_percent: float | None,
    density_lb_per_gallon: float | None = None,
) -> tuple[float | None, str]:
    """Pounds of active ingredient applied, the standard comparable measure.

    For a dry product this is simply product pounds times the AI fraction.  For
    a liquid it additionally needs the formulation density (pounds per gallon),
    which comes from the product label.  Returns ``(None, reason)`` when the
    inputs are missing, so callers can say why a total is incomplete instead of
    printing a figure that silently omits products.
    """
    if ingredient_percent is None:
        return None, "the product's active-ingredient percentage is not known"
    fraction = ingredient_percent / 100.0

    if quantity.pounds is not None:
        return quantity.pounds * fraction, "computed from product weight"
    if quantity.gallons is not None:
        if density_lb_per_gallon is None:
            return None, (
                "the product is liquid and its formulation density (pounds per gallon) "
                "is not known, so applied pounds cannot be computed"
            )
        return quantity.gallons * density_lb_per_gallon * fraction, (
            "computed from product volume and label density"
        )
    return None, "the reported quantity could not be resolved to a weight or volume"


@dataclass
class QuantityTotal:
    """Running totals that keep incompatible units apart."""

    gallons: float = 0.0
    pounds: float = 0.0
    count: float = 0.0
    #: Quantities that could not be resolved, kept as ``(amount, unit)`` pairs
    #: so the UI can show "plus 40 ounces of unknown formulation".
    unresolved: list[tuple[float, str]] = field(default_factory=list)
    #: Pounds of active ingredient, where computable.
    ai_pounds: float = 0.0
    #: Number of product lines whose AI pounds could not be computed.
    ai_incomplete: int = 0
    ai_incomplete_reasons: set[str] = field(default_factory=set)

    def add(self, quantity: NormalizedQuantity) -> None:
        if quantity.gallons is not None:
            self.gallons += quantity.gallons
        if quantity.pounds is not None:
            self.pounds += quantity.pounds
        if quantity.count is not None:
            self.count += quantity.count
        if not quantity.is_resolved and quantity.original_amount is not None:
            self.unresolved.append((quantity.original_amount, quantity.original_unit or ""))

    def add_ai(self, pounds: float | None, reason: str) -> None:
        if pounds is None:
            self.ai_incomplete += 1
            self.ai_incomplete_reasons.add(reason)
        else:
            self.ai_pounds += pounds

    @property
    def ai_is_complete(self) -> bool:
        return self.ai_incomplete == 0

    def to_dict(self) -> dict:
        grouped: dict[str, float] = {}
        line_counts: dict[str, int] = {}
        for amount, unit in self.unresolved:
            key = unit or "(no unit)"
            grouped[key] = grouped.get(key, 0.0) + amount
            line_counts[key] = line_counts.get(key, 0) + 1
        return {
            "gallons": round(self.gallons, 4) or None,
            "pounds": round(self.pounds, 4) or None,
            "count": round(self.count, 4) or None,
            # Grouped by unit: a list of hundreds of individual ounce readings
            # is noise, but "1,847 ounces of unknown formulation" is a fact the
            # reader needs in order to know the total is incomplete.
            "unresolved": [
                {"amount": round(total, 4), "unit": unit, "lines": counts[unit]}
                for unit, total in sorted(grouped.items())
                for counts in [line_counts]
            ],
            "active_ingredient_pounds": round(self.ai_pounds, 4) if self.ai_pounds else None,
            "active_ingredient_complete": self.ai_is_complete,
            "active_ingredient_gaps": sorted(self.ai_incomplete_reasons),
        }
