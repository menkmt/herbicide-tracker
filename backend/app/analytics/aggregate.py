"""Totals and rollups across applications.

Answers the questions the public site and the admin dashboard both need:
how much of each chemical was applied, over how many acres, in which county,
by which company, by air or by ground, and how that changes year to year.

Every total is computed the careful way described in :mod:`app.core.units`:
incompatible units are kept apart, and pounds of active ingredient — the only
figure comparable between products — is reported alongside a note when it is
incomplete.  A total that quietly omits a product is worse than one that says
it is missing something.

The aggregator works on extracted records directly, so it can be exercised
against real data without a database, and the API layer reuses it verbatim.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from app.core.site_category import SiteCategory, classify_site
from app.core.units import (
    QuantityTotal,
    active_ingredient_pounds,
    normalize_quantity,
)
from app.extraction.base import ApplicationMethod, ProductApplication, PurRecord


class ProductInfo(Protocol):
    """What the chemical enrichment layer supplies about a product."""

    formulation: str | None
    density_lb_per_gallon: float | None

    def ingredient_percentages(self) -> Sequence[tuple[str, float | None]]:
        """``[(active ingredient name, percentage by weight), ...]``."""
        ...


ProductLookup = Callable[[ProductApplication], ProductInfo | None]


@dataclass
class Totals:
    """Accumulated figures for one group of applications."""

    key: Any = None
    label: str = ""
    applications: int = 0
    product_lines: int = 0
    acres_treated: float = 0.0
    #: Acreage is often repeated across the product lines of one application;
    #: counting it once per application is what makes this figure meaningful.
    acres_is_partial: bool = False
    aerial_applications: int = 0
    ground_applications: int = 0
    unknown_method_applications: int = 0
    planned_applications: int = 0
    quantity: QuantityTotal = field(default_factory=QuantityTotal)
    #: Per-product and per-active-ingredient quantity totals.
    by_product: dict[str, QuantityTotal] = field(default_factory=dict)
    by_ingredient: dict[str, QuantityTotal] = field(default_factory=dict)
    product_counts: Counter = field(default_factory=Counter)
    ingredient_counts: Counter = field(default_factory=Counter)
    sites: set[str] = field(default_factory=set)
    counties: set[str] = field(default_factory=set)
    operators: set[str] = field(default_factory=set)
    applicators: set[str] = field(default_factory=set)
    first_date: date | None = None
    last_date: date | None = None

    @property
    def aerial_share(self) -> float | None:
        known = self.aerial_applications + self.ground_applications
        return round(self.aerial_applications / known, 4) if known else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "applications": self.applications,
            "product_lines": self.product_lines,
            "acres_treated": round(self.acres_treated, 2),
            "acres_is_partial": self.acres_is_partial,
            "aerial_applications": self.aerial_applications,
            "ground_applications": self.ground_applications,
            "unknown_method_applications": self.unknown_method_applications,
            "aerial_share": self.aerial_share,
            "planned_applications": self.planned_applications,
            "quantity": self.quantity.to_dict(),
            "by_product": {
                name: total.to_dict()
                for name, total in sorted(self.by_product.items(), key=lambda kv: kv[0])
            },
            "by_active_ingredient": {
                name: total.to_dict()
                for name, total in sorted(self.by_ingredient.items(), key=lambda kv: kv[0])
            },
            "top_products": self.product_counts.most_common(10),
            "top_active_ingredients": self.ingredient_counts.most_common(10),
            "distinct_sites": len(self.sites),
            "counties": sorted(self.counties),
            "distinct_operators": len(self.operators),
            "distinct_applicators": len(self.applicators),
            "first_date": self.first_date.isoformat() if self.first_date else None,
            "last_date": self.last_date.isoformat() if self.last_date else None,
        }


def _add_dates(totals: Totals, record: PurRecord) -> None:
    start, end = record.date_range
    for value in (start, end):
        if value is None:
            continue
        if totals.first_date is None or value < totals.first_date:
            totals.first_date = value
        if totals.last_date is None or value > totals.last_date:
            totals.last_date = value


def accumulate(
    totals: Totals,
    record: PurRecord,
    *,
    lookup: ProductLookup | None = None,
) -> None:
    """Fold one PUR record into a running set of totals."""
    totals.applications += 1
    _add_dates(totals, record)

    if record.is_planned:
        totals.planned_applications += 1

    if record.method == ApplicationMethod.AERIAL:
        totals.aerial_applications += 1
    elif record.method == ApplicationMethod.GROUND:
        totals.ground_applications += 1
    else:
        totals.unknown_method_applications += 1

    # Acreage is counted once per application. The record-level treated amount
    # is preferred; falling back to the maximum across product lines avoids
    # multiplying the acreage by the number of products tank-mixed onto it.
    acres = record.treated_amount
    if acres is None:
        line_acres = [p.treated_amount for p in record.products if p.treated_amount]
        acres = max(line_acres) if line_acres else None
    if acres is None:
        totals.acres_is_partial = True
    else:
        totals.acres_treated += acres

    if record.mtrs:
        totals.sites.add(record.mtrs)
    if record.county_name:
        totals.counties.add(record.county_name)
    if record.operator_name:
        totals.operators.add(record.operator_name)
    if record.applicator_name:
        totals.applicators.add(record.applicator_name)

    for product in record.products:
        totals.product_lines += 1
        name = product.product_name or product.epa_reg_no or "(unnamed product)"
        totals.product_counts[name] += 1

        info = lookup(product) if lookup else None
        quantity = normalize_quantity(
            product.quantity,
            product.quantity_units,
            product_name=product.product_name,
            formulation=getattr(info, "formulation", None),
        )
        totals.quantity.add(quantity)
        totals.by_product.setdefault(name, QuantityTotal()).add(quantity)

        ingredients = list(info.ingredient_percentages()) if info else []
        if not ingredients:
            totals.quantity.add_ai(
                None, "the product's active ingredients have not been identified yet"
            )
            continue

        density = getattr(info, "density_lb_per_gallon", None)
        for ingredient_name, percent in ingredients:
            totals.ingredient_counts[ingredient_name] += 1
            pounds, reason = active_ingredient_pounds(
                quantity, ingredient_percent=percent, density_lb_per_gallon=density
            )
            totals.quantity.add_ai(pounds, reason)
            ingredient_total = totals.by_ingredient.setdefault(ingredient_name, QuantityTotal())
            ingredient_total.add(quantity)
            ingredient_total.add_ai(pounds, reason)


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

KeyFn = Callable[[PurRecord], Iterable[tuple[Any, str]]]


def by_county(record: PurRecord) -> list[tuple[Any, str]]:
    name = record.county_name or "(county not reported)"
    return [(name, name)]


def by_year(record: PurRecord) -> list[tuple[Any, str]]:
    start, _ = record.date_range
    if start is None:
        return [("unknown", "(year not reported)")]
    return [(start.year, str(start.year))]


def by_operator(record: PurRecord) -> list[tuple[Any, str]]:
    """Group by permittee/operator — the landowner in forestry PURs."""
    name = record.operator_name or "(operator not reported)"
    return [(name, name)]


def by_landowner(record: PurRecord) -> list[tuple[Any, str]]:
    """Group by the property owner named in the record's Location field.

    Some counties put the landowner in ``Location`` and the managing company in
    ``Permittee``; where a location name exists it is the better landowner
    signal.
    """
    name = record.location_text or record.operator_name or "(landowner not reported)"
    return [(name, name)]


def by_applicator(record: PurRecord) -> list[tuple[Any, str]]:
    name = record.applicator_name or "(applicator not reported)"
    return [(name, name)]


def by_method(record: PurRecord) -> list[tuple[Any, str]]:
    return [(record.method, record.method.replace("_", " ").title())]


def by_site_category(record: PurRecord) -> list[tuple[Any, str]]:
    classification = classify_site(record.commodity_code, record.commodity)
    return [(classification.category, classification.label)]


def by_product(record: PurRecord) -> list[tuple[Any, str]]:
    """One record contributes to every product applied under it."""
    keys = []
    for product in record.products:
        name = product.product_name or product.epa_reg_no or "(unnamed product)"
        keys.append((name, name))
    return keys or [("(no product)", "(no product)")]


def by_township(record: PurRecord) -> list[tuple[Any, str]]:
    if not record.site:
        return [("unknown", "(location not decoded)")]
    key = (
        f"T{record.site.township}{record.site.township_dir} "
        f"R{record.site.range}{record.site.range_dir}"
    )
    return [(key, key)]


DIMENSIONS: dict[str, KeyFn] = {
    "county": by_county,
    "year": by_year,
    "operator": by_operator,
    "landowner": by_landowner,
    "applicator": by_applicator,
    "method": by_method,
    "site_category": by_site_category,
    "product": by_product,
    "township": by_township,
}


def aggregate(
    records: Iterable[PurRecord],
    *,
    dimension: str | KeyFn | None = None,
    lookup: ProductLookup | None = None,
) -> dict[Any, Totals]:
    """Group records and total them.

    With no dimension this returns a single ``{None: Totals}`` — the statewide
    figure.  A record that falls into several groups on a dimension (several
    products, say) is counted in each, which is what "total applied of this
    chemical" means.
    """
    if dimension is None:
        overall = Totals(key=None, label="All applications")
        for record in records:
            accumulate(overall, record, lookup=lookup)
        return {None: overall}

    key_fn = DIMENSIONS[dimension] if isinstance(dimension, str) else dimension
    groups: dict[Any, Totals] = {}
    for record in records:
        for key, label in key_fn(record):
            totals = groups.get(key)
            if totals is None:
                totals = Totals(key=key, label=label)
                groups[key] = totals
            accumulate(totals, record, lookup=lookup)
    return groups


def leaderboard(
    groups: dict[Any, Totals],
    *,
    metric: str = "acres_treated",
    limit: int = 25,
) -> list[Totals]:
    """Sort groups by a metric, biggest first — the shape the UI tables want."""
    def value(totals: Totals) -> float:
        if metric == "acres_treated":
            return totals.acres_treated
        if metric == "applications":
            return float(totals.applications)
        if metric == "active_ingredient_pounds":
            return totals.quantity.ai_pounds
        if metric == "aerial_applications":
            return float(totals.aerial_applications)
        raise ValueError(f"unknown metric {metric!r}")

    return sorted(groups.values(), key=value, reverse=True)[:limit]


def statewide_summary(
    records: Sequence[PurRecord],
    *,
    lookup: ProductLookup | None = None,
) -> dict[str, Any]:
    """The headline figures for the tracker's front page."""
    overall = aggregate(records, lookup=lookup)[None]
    counties = aggregate(records, dimension="county", lookup=lookup)
    categories = aggregate(records, dimension="site_category", lookup=lookup)
    return {
        "totals": overall.to_dict(),
        "counties": [t.to_dict() for t in leaderboard(counties, limit=100)],
        "site_categories": [t.to_dict() for t in leaderboard(categories, limit=20)],
        "forestry_share": (
            round(
                categories[SiteCategory.FORESTRY].applications / overall.applications, 4
            )
            if SiteCategory.FORESTRY in categories and overall.applications
            else None
        ),
    }
