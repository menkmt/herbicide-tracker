"""Yearly herbicide totals: statewide, by county and by landowner.

What is counted:

* published applications only, and only what use reports say was applied —
  notices of intent are plans, not use;
* herbicide product lines. Tank adjuvants (surfactants, oils, dyes) are
  totalled separately and rodent baits are left out entirely;
* gallons and pounds of *product*, kept apart and never added together. An
  ounce is read as a fluid or weight ounce from the product's formulation;
  a quantity that still cannot be resolved is reported as outstanding, so a
  total never silently shrinks;
* a line whose rate per acre is more than ten times what the same product
  is typically reported at (29,025 lb of Velpar on 83 acres, against the
  usual 3.5 lb an acre) is almost always a dropped decimal in the county's
  data. It is held out of the totals and listed, never silently corrected.

Pounds of active ingredient need label percentages most products do not
have on file yet, so they are not totalled here.

A product with two active ingredients (2,4-D + triclopyr) is one chemical
mix, counted once under that mix, so the mixes add up to the yearly total.
"""

from __future__ import annotations

import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import rate_limit
from app.chemicals.scope import is_rodenticide
from app.core.access import Principal
from app.core.coverage import DocumentKind
from app.core.normalize import company_key
from app.core.units import normalize_quantity
from app.db import get_session
from app.models import (
    ActiveIngredient,
    ApplicationCluster,
    ClusterRecord,
    County,
    Product,
    ProductIngredient,
    PurProduct,
    PurRecord,
)

router = APIRouter(prefix="/api", tags=["tallies"])

PUBLISHED = ApplicationCluster.status == "published"


@dataclass
class Bucket:
    applications: set[int] = field(default_factory=set)
    acres: float = 0.0
    gallons: float = 0.0
    pounds: float = 0.0
    unresolved: dict[str, list[float]] = field(default_factory=dict)

    def add_application(self, cluster_id: int, acres: float | None) -> None:
        if cluster_id not in self.applications:
            self.applications.add(cluster_id)
            self.acres += acres or 0.0

    def add_line(self, line: _Line) -> None:
        self.add_application(line.cluster_id, None)
        if line.gallons is not None:
            self.gallons += line.gallons
        elif line.pounds is not None:
            self.pounds += line.pounds
        elif line.quantity is not None:
            unit = (line.units or "no unit").strip().lower() or "no unit"
            entry = self.unresolved.setdefault(unit, [0.0, 0])
            entry[0] += line.quantity
            entry[1] += 1

    def to_dict(self, *, with_acres: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "applications": len(self.applications),
            "gallons": round(self.gallons, 1),
            "pounds": round(self.pounds, 1),
            "unresolved": [
                {"unit": unit, "amount": round(amount, 2), "lines": int(lines)}
                for unit, (amount, lines) in sorted(self.unresolved.items())
            ],
        }
        if with_acres:
            out["acres"] = round(self.acres, 1)
        return out


@dataclass(frozen=True)
class _Cluster:
    id: int
    year: int
    county: str | None
    county_slug: str | None
    owner: str | None
    owner_key: str | None
    acres: float | None


@dataclass(frozen=True)
class _Line:
    cluster_id: int
    record_id: int
    kind: str  # herbicide | adjuvant
    label: str
    ingredients: tuple[tuple[str, str], ...]
    gallons: float | None
    pounds: float | None
    quantity: float | None
    units: str | None
    product: str | None = None
    acres: float | None = None
    document_number: str | None = None
    date: str | None = None

    @property
    def amount(self) -> tuple[float, str] | None:
        if self.gallons is not None:
            return self.gallons, "gallons"
        if self.pounds is not None:
            return self.pounds, "pounds"
        return None


#: A rate this many times the product's typical rate is treated as an error.
OUTLIER_FACTOR = 10
#: Lines of a product needed before its typical rate is trusted.
OUTLIER_MIN_LINES = 3


@dataclass
class _Snapshot:
    fingerprint: tuple
    clusters: dict[int, _Cluster]
    lines: list[_Line]
    held_out: list[dict]


_cache: dict[str, _Snapshot] = {}
_lock = threading.Lock()


def _fingerprint(session: Session) -> tuple:
    count, latest = session.execute(
        select(func.count(ApplicationCluster.id), func.max(ApplicationCluster.updated_at))
        .where(PUBLISHED)
    ).one()
    lines = session.scalar(select(func.count(PurProduct.id)))
    return (str(session.get_bind().url), count, latest, lines)


def _load(session: Session) -> _Snapshot:
    """Everything the tallies need, read once and reused until the published
    data changes."""
    fingerprint = _fingerprint(session)
    with _lock:
        cached = _cache.get("snapshot")
        if cached is not None and cached.fingerprint == fingerprint:
            return cached

    clusters: dict[int, _Cluster] = {}
    for cid, start, county, county_slug, owner, owner_key, acres in session.execute(
        select(ApplicationCluster.id, ApplicationCluster.date_start, County.name, County.slug,
               ApplicationCluster.owner_name, ApplicationCluster.owner_key,
               ApplicationCluster.total_acres)
        .outerjoin(County, County.id == ApplicationCluster.county_id)
        .where(PUBLISHED, ApplicationCluster.is_planned.is_not(True),
               ApplicationCluster.date_start.is_not(None))
    ).all():
        key = owner_key or (company_key(owner) or None if owner else None)
        clusters[cid] = _Cluster(cid, start.year, county, county_slug, owner, key, acres)

    products: dict[int, tuple[bool, tuple[tuple[str, str], ...], str | None]] = {}
    ingredient_rows = session.execute(
        select(ProductIngredient.product_id, ActiveIngredient.name, ActiveIngredient.slug)
        .join(ActiveIngredient, ActiveIngredient.id == ProductIngredient.ingredient_id)
    ).all()
    by_product: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for pid, name, slug in ingredient_rows:
        by_product[pid].append((name, slug))
    for pid, is_adjuvant, formulation in session.execute(
        select(Product.id, Product.is_adjuvant, Product.formulation)
    ).all():
        products[pid] = (bool(is_adjuvant), tuple(sorted(set(by_product.get(pid, [])))),
                         formulation)

    lines: list[_Line] = []
    for (cid, rid, pid, pname, gallons, pounds, quantity, units, acres, doc,
         start) in session.execute(
        select(ClusterRecord.cluster_id, PurRecord.id, PurProduct.product_id,
               PurProduct.product_name, PurProduct.gallons, PurProduct.pounds,
               PurProduct.quantity, PurProduct.quantity_units, PurRecord.treated_amount,
               PurRecord.document_number, PurRecord.date_start)
        .join(PurRecord, PurRecord.id == PurProduct.record_id)
        .join(ClusterRecord, ClusterRecord.record_id == PurRecord.id)
        .join(ApplicationCluster, ApplicationCluster.id == ClusterRecord.cluster_id)
        .where(PUBLISHED, PurRecord.record_kind != DocumentKind.NOTICE_OF_INTENT)
    ).all():
        if cid not in clusters:
            continue
        is_adjuvant, ingredients, formulation = (
            products.get(pid, (False, (), None)) if pid else (False, (), None))
        if gallons is None and pounds is None and quantity is not None:
            # Resolved at import only if the product was known then; an ounce
            # needs the formulation, which may have been looked up since.
            resolved = normalize_quantity(float(quantity), units, product_name=pname,
                                          formulation=formulation)
            gallons, pounds = resolved.gallons, resolved.pounds
        if is_rodenticide([n for n, _ in ingredients], pname):
            continue
        if is_adjuvant:
            kind, label = "adjuvant", (pname or "Unnamed additive").strip()
        elif ingredients:
            kind, label = "herbicide", " + ".join(n for n, _ in ingredients)
        else:
            kind, label = "herbicide", "Not yet identified"
        lines.append(_Line(cid, rid, kind, label, ingredients if kind == "herbicide" else (),
                           float(gallons) if gallons is not None else None,
                           float(pounds) if pounds is not None else None,
                           float(quantity) if quantity is not None else None, units,
                           product=pname, acres=float(acres) if acres else None,
                           document_number=doc, date=start.isoformat() if start else None))

    lines, held_out = _hold_out_errors(lines, clusters)
    snapshot = _Snapshot(fingerprint, clusters, lines, held_out)
    with _lock:
        _cache["snapshot"] = snapshot
    return snapshot


def _hold_out_errors(lines: list[_Line], clusters: dict[int, _Cluster]
                     ) -> tuple[list[_Line], list[dict]]:
    """Separate lines whose rate per acre is far outside the product's norm."""
    rates: dict[tuple[str, str], list[float]] = defaultdict(list)
    for line in lines:
        if line.amount and line.acres:
            rates[((line.product or line.label).upper(), line.amount[1])].append(
                line.amount[0] / line.acres)
    typical = {key: median(values) for key, values in rates.items()
               if len(values) >= OUTLIER_MIN_LINES}

    kept: list[_Line] = []
    held: list[dict] = []
    for line in lines:
        key = ((line.product or line.label).upper(), line.amount[1]) if line.amount else None
        norm = typical.get(key) if key else None
        if norm and line.acres and line.amount[0] / line.acres > OUTLIER_FACTOR * norm:
            c = clusters[line.cluster_id]
            held.append({
                "cluster_id": line.cluster_id,
                "document_number": line.document_number, "date": line.date,
                "county": c.county, "county_slug": c.county_slug, "owner_key": c.owner_key,
                "product": line.product, "amount": round(line.amount[0], 2),
                "unit": line.amount[1], "acres": line.acres,
                "rate": round(line.amount[0] / line.acres, 2), "typical_rate": round(norm, 2),
            })
            continue
        kept.append(line)
    return kept, held


def _by_year(buckets: dict[int, Bucket], years: list[int], **kw) -> dict[str, Any]:
    total = Bucket()
    for bucket in buckets.values():
        for cid in bucket.applications:
            total.applications.add(cid)
        total.acres += bucket.acres
        total.gallons += bucket.gallons
        total.pounds += bucket.pounds
        for unit, (amount, count) in bucket.unresolved.items():
            entry = total.unresolved.setdefault(unit, [0.0, 0])
            entry[0] += amount
            entry[1] += count
    return {
        "by_year": {str(y): buckets[y].to_dict(**kw) for y in years if y in buckets},
        "all": total.to_dict(**kw),
    }


def compute_tallies(session: Session, *, county: str | None = None,
                    owner: str | None = None, landowner_limit: int = 250) -> dict[str, Any]:
    snap = _load(session)
    owner_key = company_key(owner) if owner else None

    def keep(c: _Cluster) -> bool:
        if county and c.county_slug != county:
            return False
        return not (owner_key and c.owner_key != owner_key)

    clusters = {cid: c for cid, c in snap.clusters.items() if keep(c)}
    totals: dict[int, Bucket] = defaultdict(Bucket)
    adjuvants: dict[int, Bucket] = defaultdict(Bucket)
    chemicals: dict[str, dict[int, Bucket]] = defaultdict(lambda: defaultdict(Bucket))
    chemical_ingredients: dict[str, tuple] = {}
    counties: dict[str, dict[int, Bucket]] = defaultdict(lambda: defaultdict(Bucket))
    county_names: dict[str, str] = {}
    owners: dict[str, dict[int, Bucket]] = defaultdict(lambda: defaultdict(Bucket))
    owner_names: dict[str, Counter] = defaultdict(Counter)

    for c in clusters.values():
        totals[c.year].add_application(c.id, c.acres)
        if c.county_slug:
            counties[c.county_slug][c.year].add_application(c.id, c.acres)
            county_names[c.county_slug] = c.county or c.county_slug
        if c.owner_key:
            owners[c.owner_key][c.year].add_application(c.id, c.acres)
            owner_names[c.owner_key][c.owner or c.owner_key] += 1

    for line in snap.lines:
        c = clusters.get(line.cluster_id)
        if c is None:
            continue
        if line.kind == "adjuvant":
            adjuvants[c.year].add_line(line)
            continue
        totals[c.year].add_line(line)
        chemicals[line.label][c.year].add_line(line)
        chemical_ingredients[line.label] = line.ingredients
        if c.county_slug:
            counties[c.county_slug][c.year].add_line(line)
        if c.owner_key:
            owners[c.owner_key][c.year].add_line(line)

    years = sorted(totals)
    held_out = [
        h for h in snap.held_out
        if h["cluster_id"] in clusters
    ]
    slugs = dict(session.execute(
        select(ApplicationCluster.id, ApplicationCluster.slug)
        .where(ApplicationCluster.id.in_({h["cluster_id"] for h in held_out}))
    ).all()) if held_out else {}
    held_rows = [
        {**{k: v for k, v in h.items() if k not in ("cluster_id", "owner_key")},
         "url": f"/application/{slugs.get(h['cluster_id'])}" if h["cluster_id"] in slugs else None}
        for h in held_out
    ]

    def size(buckets: dict[int, Bucket]) -> tuple:
        return (sum(b.gallons for b in buckets.values()),
                sum(b.pounds for b in buckets.values()),
                sum(len(b.applications) for b in buckets.values()))

    chemical_rows = [
        {"label": label,
         "ingredients": [{"name": n, "slug": s, "url": f"/chemical/{s}"}
                         for n, s in chemical_ingredients.get(label, ())],
         **_by_year(buckets, years, with_acres=False)}
        for label, buckets in sorted(chemicals.items(), key=lambda kv: size(kv[1]), reverse=True)
    ]
    county_rows = [
        {"name": county_names[slug], "slug": slug, **_by_year(buckets, years)}
        for slug, buckets in sorted(counties.items(), key=lambda kv: size(kv[1]), reverse=True)
    ]
    ranked_owners = sorted(owners.items(), key=lambda kv: size(kv[1]), reverse=True)
    owner_rows = [
        {"name": owner_names[key].most_common(1)[0][0], "key": key, **_by_year(buckets, years)}
        for key, buckets in ranked_owners[:landowner_limit]
    ]

    scope: dict[str, Any] = {"county": None, "owner": None}
    if county:
        scope["county"] = {"slug": county, "name": county_names.get(county)
                           or session.scalar(select(County.name).where(County.slug == county))}
    if owner_key:
        scope["owner"] = {"key": owner_key,
                          "name": owner_rows[0]["name"] if owner_rows else owner}

    return {
        "scope": scope,
        "years": years,
        "totals": _by_year(totals, years),
        "adjuvants": _by_year(adjuvants, years, with_acres=False),
        "chemicals": chemical_rows,
        "counties": county_rows,
        "landowners": owner_rows,
        "landowner_count": len(owners),
        "held_out": held_rows,
    }


def owner_tally(session: Session, owner_key: str | None) -> dict[str, Any] | None:
    """The compact per-year tally on a landowner's card."""
    if not owner_key:
        return None
    data = compute_tallies(session, owner=owner_key, landowner_limit=1)
    if not data["years"]:
        return None
    return {
        "key": owner_key,
        "years": [{"year": int(y), **row} for y, row in data["totals"]["by_year"].items()],
        "all": data["totals"]["all"],
        "top_chemicals": [row["label"] for row in data["chemicals"][:3]],
        "held_out": len(data["held_out"]),
    }


@router.get("/tallies")
def tallies(
    session: Session = Depends(get_session),
    _: Principal = Depends(rate_limit),
    county: str | None = Query(None, max_length=64),
    owner: str | None = Query(None, max_length=255),
) -> dict[str, Any]:
    return compute_tallies(session, county=county, owner=owner)
