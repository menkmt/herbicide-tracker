"""Deciding which PUR records describe one real-world application.

A forestry herbicide project is reported to the county as many separate use
reports — one per site, sometimes one per day — but to the public it is one
event: *Motor Sheep Biomass, October 2024, 1,050 acres*.  Grouping them is what
turns a spreadsheet into something a person can read.

The grouping is a score, not a rule, because the signals are individually
fallible: two projects can share an owner, and one project can span two
operators.  Pairs of records are scored on the weighted signals from the build
plan, and the score decides between three outcomes:

* **high** — cluster them automatically;
* **medium** — propose the cluster and send it to review;
* **low** — leave them separate.

Nothing here destroys or rewrites a source record.  A cluster is a parent that
*references* PUR records, so merging, splitting and moving records between
clusters are all reversible.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from app.core.normalize import compare_companies
from app.core.siteid import sections_are_adjacent
from app.extraction.base import PurRecord


@dataclass(frozen=True)
class ClusterWeights:
    """Signal weights, as specified in the build plan and configurable here.

    Defaults are exactly the plan's values.  ``nearby_date_*`` is an optional
    extension for projects that run over more than two days; it is disabled by
    default so the out-of-the-box behaviour is precisely the specified scheme.
    """

    same_owner: int = 40
    same_dates: int = 25
    adjacent_sections: int = 20
    same_applicator: int = 10
    same_products: int = 10
    same_pca: int = 5
    same_permit: int = 20

    #: Dates this many days apart or fewer score :attr:`same_dates`.
    date_window_days: int = 1
    #: Optional second tier for longer projects; 0 disables it.
    nearby_date_days: int = 7
    nearby_date_weight: int = 0

    #: At or above this, records are clustered automatically.
    auto_threshold: int = 75
    #: At or above this (but below auto), the cluster is proposed for review.
    review_threshold: int = 45

    #: Time and place are treated as *necessary*, not merely contributory.
    #:
    #: The weighted signals alone over-cluster badly on real data. One
    #: operator working one permit with one applicator and a standard tank mix
    #: scores 80 points before time or place is considered at all — enough to
    #: fuse an entire year of separate projects into a single "application".
    #: Those attributes are constants for that operator, so they carry no
    #: information about which records belong together.
    #:
    #: An application is an event bounded in time and space, so:
    #:   * automatic clustering requires the dates to be close;
    #:   * a review proposal requires either close dates or touching sections.
    #: Anything failing both is the same people doing different work.
    require_date_proximity_to_cluster: bool = True
    require_proximity_to_propose: bool = True
    #: A pair may only be *proposed* as the same application if its dates are
    #: within this many days. Section adjacency is a supporting signal, not a
    #: substitute for being close in time: two adjacent sections treated four
    #: months apart are two applications, and proposing otherwise buries the
    #: reviewer in suggestions that are all wrong.
    propose_date_days: int = 30

    def max_score(self) -> int:
        return (
            self.same_owner
            + self.same_dates
            + self.adjacent_sections
            + self.same_applicator
            + self.same_products
            + self.same_pca
            + self.same_permit
        )


DEFAULT_WEIGHTS = ClusterWeights()


class Outcome:
    AUTO = "high"
    REVIEW = "medium"
    SEPARATE = "low"


@dataclass
class SignalScore:
    """One signal's contribution, kept so the admin screen can explain a score."""

    name: str
    points: int
    matched: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal": self.name,
            "points": self.points,
            "matched": self.matched,
            "detail": self.detail,
        }


@dataclass
class PairScore:
    """The full, explainable comparison of two PUR records."""

    left: str
    right: str
    total: int
    outcome: str
    signals: list[SignalScore] = field(default_factory=list)

    @property
    def matched_signals(self) -> list[SignalScore]:
        return [s for s in self.signals if s.matched]

    def explain(self) -> str:
        """One line a reviewer can act on."""
        if not self.matched_signals:
            return "no shared signals"
        return f"{self.total} points: " + ", ".join(
            f"{s.name} (+{s.points})" for s in self.matched_signals
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "left": self.left,
            "right": self.right,
            "total": self.total,
            "outcome": self.outcome,
            "explanation": self.explain(),
            "signals": [s.to_dict() for s in self.signals],
        }


def _record_key(record: PurRecord, index: int) -> str:
    return record.document_number or f"record-{index}"


def _dates_within(a: PurRecord, b: PurRecord, days: int) -> tuple[bool, str]:
    a_start, a_end = a.date_range
    b_start, b_end = b.date_range
    if not a_start or not b_start:
        return False, "one record has no readable date"
    # Compare the closest edges, so a two-day project overlapping a one-day one
    # still counts as same-time.
    gap = min(
        abs((a_start - b_start).days),
        abs((a_start - (b_end or b_start)).days),
        abs(((a_end or a_start) - b_start).days),
    )
    return gap <= days, f"{gap} day(s) apart"


def _product_overlap(a: PurRecord, b: PurRecord) -> tuple[bool, str]:
    """Products match when the registration numbers overlap.

    Registration numbers are compared rather than names because the same
    product is written several ways across counties and years, while the EPA
    number is stable.
    """
    left = {p.base_epa_reg_no for p in a.products if p.base_epa_reg_no}
    right = {p.base_epa_reg_no for p in b.products if p.base_epa_reg_no}
    if not left or not right:
        left = {(p.product_name or "").upper() for p in a.products if p.product_name}
        right = {(p.product_name or "").upper() for p in b.products if p.product_name}
    if not left or not right:
        return False, "one record lists no products"
    shared = left & right
    if not shared:
        return False, "no products in common"
    return True, f"{len(shared)} product(s) in common"


def _sections_close(a: PurRecord, b: PurRecord) -> tuple[bool, str]:
    if not a.site or not b.site:
        return False, "one record has no decoded location"
    if a.site.trs == b.site.trs:
        return True, f"same section ({a.site.trs})"
    if sections_are_adjacent(a.site, b.site):
        return True, f"adjacent sections ({a.site.trs} / {b.site.trs})"
    return False, f"sections are not adjacent ({a.site.trs} / {b.site.trs})"


def score_pair(
    a: PurRecord,
    b: PurRecord,
    *,
    weights: ClusterWeights = DEFAULT_WEIGHTS,
    left_key: str = "a",
    right_key: str = "b",
) -> PairScore:
    """Score how likely two records are to be the same real application."""
    signals: list[SignalScore] = []

    owner_match = compare_companies(a.operator_name, b.operator_name)
    signals.append(
        SignalScore(
            "same property owner",
            weights.same_owner if owner_match.matched else 0,
            owner_match.matched,
            owner_match.reason,
        )
    )

    same_dates, date_detail = _dates_within(a, b, weights.date_window_days)
    if same_dates:
        signals.append(SignalScore("application dates", weights.same_dates, True, date_detail))
    elif weights.nearby_date_weight:
        nearby, nearby_detail = _dates_within(a, b, weights.nearby_date_days)
        signals.append(
            SignalScore(
                "application dates (nearby)",
                weights.nearby_date_weight if nearby else 0,
                nearby,
                nearby_detail,
            )
        )
    else:
        signals.append(SignalScore("application dates", 0, False, date_detail))

    close, close_detail = _sections_close(a, b)
    signals.append(
        SignalScore(
            "sections touch or coincide",
            weights.adjacent_sections if close else 0,
            close,
            close_detail,
        )
    )

    applicator_match = compare_companies(a.applicator_name, b.applicator_name)
    signals.append(
        SignalScore(
            "same applicator",
            weights.same_applicator if applicator_match.matched else 0,
            applicator_match.matched,
            applicator_match.reason,
        )
    )

    products_match, products_detail = _product_overlap(a, b)
    signals.append(
        SignalScore(
            "same products",
            weights.same_products if products_match else 0,
            products_match,
            products_detail,
        )
    )

    pca_a = (a.raw.get("pca_name") if a.raw else None) or None
    pca_b = (b.raw.get("pca_name") if b.raw else None) or None
    pca_match = bool(pca_a and pca_b and compare_companies(pca_a, pca_b).matched)
    signals.append(
        SignalScore(
            "same PCA",
            weights.same_pca if pca_match else 0,
            pca_match,
            "same pest control adviser" if pca_match else "no PCA recorded on both records",
        )
    )

    permit_match = bool(
        a.permit_number and b.permit_number and a.permit_number.strip() == b.permit_number.strip()
    )
    signals.append(
        SignalScore(
            "same permit number",
            weights.same_permit if permit_match else 0,
            permit_match,
            f"permit {a.permit_number}" if permit_match else "different or missing permit numbers",
        )
    )

    total = sum(s.points for s in signals)

    date_proximate = same_dates or any(
        s.name == "application dates (nearby)" and s.matched for s in signals
    )
    # Being in the same season is the precondition for even suggesting that two
    # records describe one application.
    proposable, _ = _dates_within(a, b, weights.propose_date_days)
    proximate = date_proximate or (proposable and close)

    if total >= weights.auto_threshold and (
        date_proximate or not weights.require_date_proximity_to_cluster
    ):
        outcome = Outcome.AUTO
    elif total >= weights.review_threshold and (
        proximate or not weights.require_proximity_to_propose
    ):
        outcome = Outcome.REVIEW
    else:
        outcome = Outcome.SEPARATE

    gate_note = None
    if total >= weights.auto_threshold and outcome != Outcome.AUTO:
        gate_note = (
            "scored high enough to cluster, but the applications are not close in time, "
            "so they are treated as separate projects by the same operator"
        )
    elif total >= weights.review_threshold and outcome == Outcome.SEPARATE:
        gate_note = (
            "shares attributes but the applications are more than "
            f"{weights.propose_date_days} days apart, so they are not proposed as the "
            "same application"
        )
    if gate_note:
        signals.append(SignalScore("proximity gate", 0, False, gate_note))

    return PairScore(
        left=left_key, right=right_key, total=total, outcome=outcome, signals=signals
    )


def record_keys(records: list[PurRecord]) -> list[str]:
    return [_record_key(record, index) for index, record in enumerate(records)]


def with_threshold(weights: ClusterWeights, *, auto: int, review: int) -> ClusterWeights:
    return replace(weights, auto_threshold=auto, review_threshold=review)


__all__ = [
    "ClusterWeights",
    "DEFAULT_WEIGHTS",
    "Outcome",
    "PairScore",
    "SignalScore",
    "score_pair",
    "record_keys",
    "with_threshold",
    "date",
]
