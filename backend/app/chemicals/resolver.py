"""Turning reported product names into identified chemicals.

A use report names a product ("DU PONT VELPAR DF HERBICIDE") and its EPA
registration number ("352-581-AA").  To say anything useful — which active
ingredient was applied, how much of it, whether it is restricted — the product
has to be resolved to a registration and its ingredients.

Resolution is layered, strongest source first:

1. **Authoritative providers** (DPR's product database, EPA registration data).
   These produce ``verified`` facts.
2. **The local seed file**, which covers the products actually present in the
   Lassen records so the tracker is useful before it has network access. Seed
   values are explicitly *not* authoritative: they resolve at ``seed``
   verification, which is below the bar for automatic publication, so anything
   depending on them goes to review.
3. **Unresolved.** The product is recorded and counted, and a review item says
   the product could not be identified. It is never guessed at.

Registration numbers are matched on the *base* number: California appends a
distributor suffix, so ``2935-50176-AA`` is EPA registration ``2935-50176``
sold under a California sub-label.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import yaml

from app.core.confidence import Confidence

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SEED_PATH = DATA_DIR / "products_seed.yml"
WATCHLIST_PATH = DATA_DIR / "watchlist.yml"


class Verification:
    VERIFIED = "verified"
    SEED = "seed"
    UNRESOLVED = "unresolved"

    #: Only a verified product may be published without review.
    PUBLISHABLE = frozenset({VERIFIED})


@dataclass
class IngredientRef:
    name: str
    percent: float | None = None


@dataclass
class ResolvedProduct:
    """What the tracker knows about one product."""

    base_epa_reg_no: str | None
    name: str | None
    registrant: str | None = None
    formulation: str | None = None
    signal_word: str | None = None
    density_lb_per_gallon: float | None = None
    is_adjuvant: bool = False
    federal_restricted_use: bool | None = None
    label_url: str | None = None
    verification: str = Verification.UNRESOLVED
    source: str | None = None
    ingredients: list[IngredientRef] = field(default_factory=list)

    # -- the ProductInfo protocol the aggregator expects -------------------
    def ingredient_percentages(self) -> list[tuple[str, float | None]]:
        return [(i.name, i.percent) for i in self.ingredients]

    @property
    def is_publishable(self) -> bool:
        return self.verification in Verification.PUBLISHABLE

    @property
    def confidence(self) -> str:
        return {
            Verification.VERIFIED: Confidence.VERIFIED,
            Verification.SEED: Confidence.MEDIUM,
            Verification.UNRESOLVED: Confidence.LOW,
        }[self.verification]

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_epa_reg_no": self.base_epa_reg_no,
            "name": self.name,
            "registrant": self.registrant,
            "formulation": self.formulation,
            "signal_word": self.signal_word,
            "density_lb_per_gallon": self.density_lb_per_gallon,
            "is_adjuvant": self.is_adjuvant,
            "federal_restricted_use": self.federal_restricted_use,
            "verification": self.verification,
            "source": self.source,
            "confidence": str(self.confidence),
            "ingredients": [
                {"name": i.name, "percent": i.percent} for i in self.ingredients
            ],
        }


class ProductProvider(Protocol):
    """An authoritative source of product registration data."""

    name: str

    def lookup(self, base_reg_no: str, product_name: str | None) -> ResolvedProduct | None: ...


@lru_cache(maxsize=1)
def load_seed(path: str | None = None) -> dict[str, ResolvedProduct]:
    """Load the local seed file, keyed by base registration number."""
    source = Path(path) if path else SEED_PATH
    if not source.exists():
        return {}
    data = yaml.safe_load(source.read_text()) or {}
    products: dict[str, ResolvedProduct] = {}
    for entry in data.get("products", []):
        reg = str(entry.get("epa_reg_no") or "").strip()
        if not reg:
            continue
        products[reg] = ResolvedProduct(
            base_epa_reg_no=reg,
            name=entry.get("name"),
            registrant=entry.get("registrant"),
            formulation=entry.get("formulation"),
            signal_word=entry.get("signal_word"),
            density_lb_per_gallon=entry.get("density_lb_per_gallon"),
            is_adjuvant=bool(entry.get("is_adjuvant")),
            federal_restricted_use=entry.get("federal_restricted_use"),
            verification=entry.get("verification", Verification.SEED),
            source=entry.get("source"),
            ingredients=[
                IngredientRef(name=i["name"], percent=i.get("percent"))
                for i in (entry.get("ingredients") or [])
                if i.get("name")
            ],
        )
    return products


@lru_cache(maxsize=1)
def load_watchlist(path: str | None = None) -> dict[str, Any]:
    source = Path(path) if path else WATCHLIST_PATH
    if not source.exists():
        return {"active_ingredients": [], "products": []}
    return yaml.safe_load(source.read_text()) or {}


@dataclass
class ResolutionReport:
    """Outcome of resolving every product in an import."""

    resolved: dict[str, ResolvedProduct] = field(default_factory=dict)
    unresolved: dict[str, str] = field(default_factory=dict)
    seed_only: list[str] = field(default_factory=list)

    @property
    def unresolved_count(self) -> int:
        return len(self.unresolved)


class ProductResolver:
    """Resolves products through providers, then the seed file."""

    def __init__(self, providers: list[ProductProvider] | None = None) -> None:
        self.providers = providers or []
        self._cache: dict[str, ResolvedProduct] = {}

    def resolve(self, base_reg_no: str | None, product_name: str | None) -> ResolvedProduct:
        if not base_reg_no:
            return ResolvedProduct(
                base_epa_reg_no=None,
                name=product_name,
                verification=Verification.UNRESOLVED,
                source="no EPA registration number was reported",
            )

        if base_reg_no in self._cache:
            return self._cache[base_reg_no]

        for provider in self.providers:
            try:
                found = provider.lookup(base_reg_no, product_name)
            except Exception:
                found = None
            if found is not None:
                found.verification = Verification.VERIFIED
                found.source = found.source or provider.name
                self._cache[base_reg_no] = found
                return found

        seeded = load_seed().get(base_reg_no)
        if seeded is not None:
            # Copy so a caller cannot mutate the shared seed entry.
            resolved = ResolvedProduct(**{**seeded.__dict__, "name": seeded.name or product_name})
            self._cache[base_reg_no] = resolved
            return resolved

        unresolved = ResolvedProduct(
            base_epa_reg_no=base_reg_no,
            name=product_name,
            verification=Verification.UNRESOLVED,
            source="not found in any configured product source",
        )
        self._cache[base_reg_no] = unresolved
        return unresolved

    def resolve_all(self, products: list[tuple[str | None, str | None]]) -> ResolutionReport:
        report = ResolutionReport()
        for base_reg_no, name in products:
            resolved = self.resolve(base_reg_no, name)
            key = base_reg_no or (name or "(unnamed)")
            if resolved.verification == Verification.UNRESOLVED:
                report.unresolved[key] = (
                    f"{name or key} could not be identified: {resolved.source}"
                )
            else:
                report.resolved[key] = resolved
                if resolved.verification == Verification.SEED:
                    report.seed_only.append(key)
        return report


def ingredient_names(products: list[ResolvedProduct]) -> list[str]:
    names: list[str] = []
    for product in products:
        for ingredient in product.ingredients:
            if ingredient.name not in names:
                names.append(ingredient.name)
    return names
