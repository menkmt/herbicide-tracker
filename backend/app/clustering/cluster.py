"""Building application clusters from scored pairs.

A cluster is a *parent* that references PUR records.  Source records are never
modified, merged or deleted, so every administrator action — merge, split, move
a record — is reversible and the underlying county data stays exactly as it was
reported.

Clustering is transitive by design: if A groups with B and B with C, all three
are one project, which is how a linear strip of treated sections along a ridge
actually behaves.  To stop that transitivity from chaining unrelated work
together, only *high* confidence pairs join automatically; *medium* pairs are
recorded as proposals for a human to accept or reject.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.clustering.score import (
    DEFAULT_WEIGHTS,
    ClusterWeights,
    Outcome,
    PairScore,
    score_pair,
)
from app.core.confidence import Confidence
from app.extraction.base import ApplicationMethod, PurRecord


class _DisjointSet:
    """Union-find over record indices."""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, item: int) -> int:
        while self._parent[item] != item:
            self._parent[item] = self._parent[self._parent[item]]
            item = self._parent[item]
        return item

    def union(self, a: int, b: int) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self._parent[root_b] = root_a


@dataclass
class ApplicationCluster:
    """One public application: several PUR records treated as one project."""

    key: str
    record_indices: list[int] = field(default_factory=list)
    records: list[PurRecord] = field(default_factory=list)
    #: The scored pairs that caused these records to be grouped.
    joining_pairs: list[PairScore] = field(default_factory=list)
    #: Medium-confidence pairs linking this cluster to other records.
    proposals: list[PairScore] = field(default_factory=list)
    #: Set by an administrator; overrides the derived title.
    title_override: str | None = None
    #: Set by the forestry resolver when an official project name is found.
    project_name: str | None = None
    project_source: str | None = None

    # -- derived facts ----------------------------------------------------
    @property
    def record_count(self) -> int:
        return len(self.records)

    @property
    def owner(self) -> str | None:
        """The operator named on the records, when they agree."""
        names = {r.operator_name for r in self.records if r.operator_name}
        if len(names) == 1:
            return names.pop()
        return sorted(names)[0] if names else None

    @property
    def landowner(self) -> str | None:
        """Property owner from the Location field, where counties supply it."""
        names = {r.location_text for r in self.records if r.location_text}
        return sorted(names)[0] if len(names) == 1 else None

    @property
    def date_range(self) -> tuple[date | None, date | None]:
        starts = [d for d, _ in (r.date_range for r in self.records) if d]
        ends = [e for _, e in (r.date_range for r in self.records) if e]
        return (min(starts) if starts else None, max(ends) if ends else None)

    @property
    def total_acres(self) -> float | None:
        """Sum of treated acreage across the cluster's records.

        Acreage is summed per record, not per product line, because a tank mix
        of three products on 100 acres is 100 acres, not 300.
        """
        values = []
        for record in self.records:
            acres = record.treated_amount
            if acres is None:
                line_acres = [p.treated_amount for p in record.products if p.treated_amount]
                acres = max(line_acres) if line_acres else None
            if acres is not None:
                values.append(acres)
        return round(sum(values), 2) if values else None

    @property
    def acreage_is_partial(self) -> bool:
        return any(
            r.treated_amount is None
            and not any(p.treated_amount for p in r.products)
            for r in self.records
        )

    @property
    def method(self) -> str:
        """Aerial when any record in the project was applied by air."""
        methods = {r.method for r in self.records}
        if ApplicationMethod.AERIAL in methods:
            return ApplicationMethod.AERIAL
        if methods == {ApplicationMethod.GROUND}:
            return ApplicationMethod.GROUND
        if ApplicationMethod.GROUND in methods:
            return ApplicationMethod.GROUND
        return ApplicationMethod.UNKNOWN

    @property
    def is_mixed_method(self) -> bool:
        methods = {r.method for r in self.records} - {ApplicationMethod.UNKNOWN}
        return len(methods) > 1

    @property
    def site_ids(self) -> list[str]:
        return sorted({r.site_id for r in self.records if r.site_id})

    @property
    def mtrs_list(self) -> list[str]:
        return sorted({r.mtrs for r in self.records if r.mtrs})

    @property
    def product_names(self) -> list[str]:
        names: set[str] = set()
        for record in self.records:
            names.update(record.product_names)
        return sorted(names)

    @property
    def applicators(self) -> list[str]:
        return sorted({r.applicator_name for r in self.records if r.applicator_name})

    @property
    def county(self) -> str | None:
        counties = {r.county_name for r in self.records if r.county_name}
        return counties.pop() if len(counties) == 1 else (sorted(counties)[0] if counties else None)

    @property
    def is_planned(self) -> bool:
        """True only when every record is an unfulfilled notice of intent."""
        return bool(self.records) and all(r.is_planned for r in self.records)

    def title(self) -> str:
        """The public title for this application.

        Order matters and is set by the build plan: an administrator's override
        wins, then an official forestry project name, then the property owner.
        A name is never invented — with nothing else available the cluster is
        titled by its location.
        """
        if self.title_override:
            return self.title_override
        if self.project_name:
            return self.project_name
        owner = self.landowner or self.owner
        if owner:
            return owner
        if self.mtrs_list:
            return f"Application at {self.mtrs_list[0]}"
        return "Unidentified application"

    @property
    def title_basis(self) -> str:
        """How the title was chosen — shown on the page so it is never opaque."""
        if self.title_override:
            return "set by a Protect Lassen administrator"
        if self.project_name:
            return self.project_source or "official forestry project record"
        if self.landowner or self.owner:
            return "property owner named on the pesticide use report"
        return "location only; no owner or project name was reported"

    @property
    def confidence(self) -> Confidence:
        """A cluster is only as certain as its weakest joining pair."""
        if len(self.records) == 1:
            return Confidence.VERIFIED
        if not self.joining_pairs:
            return Confidence.MANUAL
        from app.core.confidence import weakest

        return weakest(
            *[
                Confidence.HIGH if p.outcome == Outcome.AUTO else Confidence.MEDIUM
                for p in self.joining_pairs
            ]
        )

    @property
    def needs_review(self) -> bool:
        return (
            bool(self.proposals)
            or any(r.needs_review for r in self.records)
            or self.is_mixed_method
        )

    def review_reasons(self) -> list[str]:
        reasons: list[str] = []
        if self.proposals:
            reasons.append(
                f"{len(self.proposals)} other record(s) may belong to this application"
            )
        if self.is_mixed_method:
            reasons.append("this project mixes aerial and ground applications")
        for record in self.records:
            for issue in record.issues:
                if issue.severity == "review":
                    reasons.append(f"{record.document_number or 'record'}: {issue.detail}")
        return reasons

    def to_dict(self) -> dict[str, Any]:
        start, end = self.date_range
        return {
            "key": self.key,
            "title": self.title(),
            "title_basis": self.title_basis,
            "project_name": self.project_name,
            "owner": self.owner,
            "landowner": self.landowner,
            "county": self.county,
            "date_start": start.isoformat() if start else None,
            "date_end": end.isoformat() if end else None,
            "total_acres": self.total_acres,
            "acreage_is_partial": self.acreage_is_partial,
            "method": self.method,
            "is_mixed_method": self.is_mixed_method,
            "is_planned": self.is_planned,
            "site_ids": self.site_ids,
            "mtrs": self.mtrs_list,
            "products": self.product_names,
            "applicators": self.applicators,
            "record_count": len(self.records),
            "document_numbers": [r.document_number for r in self.records if r.document_number],
            "confidence": str(self.confidence),
            "needs_review": self.needs_review,
            "review_reasons": self.review_reasons(),
            "joining_pairs": [p.to_dict() for p in self.joining_pairs],
            "proposals": [p.to_dict() for p in self.proposals],
        }


@dataclass
class ClusteringResult:
    clusters: list[ApplicationCluster] = field(default_factory=list)
    #: Every pair scored at medium — the "possible same application" queue.
    proposals: list[PairScore] = field(default_factory=list)

    @property
    def review_count(self) -> int:
        return sum(1 for c in self.clusters if c.needs_review)

    @property
    def cluster_count(self) -> int:
        return len(self.clusters)

    @property
    def proposal_count(self) -> int:
        return len(self.proposals)

    def to_dict(self) -> dict[str, Any]:
        return {
            "clusters": [c.to_dict() for c in self.clusters],
            "cluster_count": len(self.clusters),
            "review_count": self.review_count,
            "proposal_count": len(self.proposals),
        }


def _candidate_pairs(records: Sequence[PurRecord]) -> list[tuple[int, int]]:
    """Pairs worth scoring at all.

    Comparing every record with every other is quadratic and pointless: two
    records months apart in different townships cannot be one application.
    Records are blocked by permit number and by year, which keeps the work
    linear in practice while never excluding a pair that could plausibly score
    above the threshold.
    """
    blocks: dict[Any, list[int]] = {}
    for index, record in enumerate(records):
        start, _ = record.date_range
        year = start.year if start else None
        for key in {
            ("permit", record.permit_number, year) if record.permit_number else None,
            ("owner", (record.operator_name or "").upper(), year)
            if record.operator_name
            else None,
        }:
            if key is not None:
                blocks.setdefault(key, []).append(index)

    pairs: set[tuple[int, int]] = set()
    for members in blocks.values():
        # A very large block is usually one operator's whole year; still
        # bounded, and the date signal does the real separating.
        for position, left in enumerate(members):
            for right in members[position + 1 :]:
                pairs.add((left, right) if left < right else (right, left))
    return sorted(pairs)


def cluster_records(
    records: Sequence[PurRecord],
    *,
    weights: ClusterWeights = DEFAULT_WEIGHTS,
) -> ClusteringResult:
    """Group PUR records into application clusters.

    Records that match nothing become single-record clusters, which is correct:
    a lone use report is still one application.
    """
    records = list(records)
    if not records:
        return ClusteringResult()

    keys = [r.document_number or f"record-{i}" for i, r in enumerate(records)]
    union = _DisjointSet(len(records))
    joining: list[tuple[int, int, PairScore]] = []
    proposals: list[PairScore] = []

    for left, right in _candidate_pairs(records):
        score = score_pair(
            records[left],
            records[right],
            weights=weights,
            left_key=keys[left],
            right_key=keys[right],
        )
        if score.outcome == Outcome.AUTO:
            union.union(left, right)
            joining.append((left, right, score))
        elif score.outcome == Outcome.REVIEW:
            proposals.append(score)

    grouped: dict[int, ApplicationCluster] = {}
    for index, record in enumerate(records):
        root = union.find(index)
        cluster = grouped.get(root)
        if cluster is None:
            cluster = ApplicationCluster(key=keys[root])
            grouped[root] = cluster
        cluster.record_indices.append(index)
        cluster.records.append(record)

    for left, right, score in joining:
        grouped[union.find(left)].joining_pairs.append(score)

    # Attach each proposal to the cluster(s) it would affect, so a reviewer
    # sees it in context rather than as a bare pair of document numbers.
    index_to_cluster = {
        index: grouped[union.find(index)] for index in range(len(records))
    }
    for score in proposals:
        left_index = keys.index(score.left)
        right_index = keys.index(score.right)
        left_cluster = index_to_cluster[left_index]
        right_cluster = index_to_cluster[right_index]
        if left_cluster is right_cluster:
            continue
        left_cluster.proposals.append(score)
        right_cluster.proposals.append(score)

    ordered = sorted(
        grouped.values(),
        key=lambda c: (c.date_range[0] or date.max, c.title()),
    )
    return ClusteringResult(clusters=ordered, proposals=proposals)
