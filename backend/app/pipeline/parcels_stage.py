"""Deciding which parcels belong to an application.

The strategy from the build plan:

    PUR owner/operator + PUR section  =  candidate parcels

Sections are a square mile and usually contain many parcels with many owners,
so intersecting the section is only the first filter.  The decision is made on
the *owner name*, compared with the normalisation that knows "WM BEATY AND
ASSOC." and "W.M. Beaty & Associates, Inc." are one business.

What the result means matters as much as how it is computed.  A parcel
association says "this property is connected to this application", recorded
with the basis for the connection.  It never says the parcel was sprayed: a
PUR reports a section and an acreage, and an operator who treated 63 acres
inside a 640-acre section did not treat the whole property.  The public page
states this, and the acreage shown is always the reported acreage, never the
parcel's.

When the evidence does not support a confident choice, the application goes to
review with the candidates attached, rather than a parcel being guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.confidence import Confidence
from app.core.normalize import compare_companies
from app.providers.parcels.base import ParcelRecord


class ParcelOutcome:
    MATCHED = "matched"
    NEEDS_REVIEW = "needs_review"
    NO_CANDIDATES = "no_candidates"
    NO_PROVIDER = "no_provider"


@dataclass
class ScoredParcel:
    parcel: ParcelRecord
    score: float
    matched: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "apn": self.parcel.apn,
            "owner": self.parcel.owner,
            "acreage": self.parcel.acreage,
            "score": round(self.score, 4),
            "matched": self.matched,
            "reason": self.reason,
            "source": self.parcel.source,
        }


@dataclass
class ParcelSelection:
    """The outcome of matching parcels to one application."""

    outcome: str
    selected: list[ScoredParcel] = field(default_factory=list)
    candidates: list[ScoredParcel] = field(default_factory=list)
    confidence: str = Confidence.LOW
    detail: str = ""
    review_reason: str | None = None

    @property
    def apns(self) -> list[str]:
        return [s.parcel.apn for s in self.selected]

    @property
    def needs_review(self) -> bool:
        return self.outcome != ParcelOutcome.MATCHED

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "confidence": self.confidence,
            "detail": self.detail,
            "review_reason": self.review_reason,
            "apns": self.apns,
            "selected": [s.to_dict() for s in self.selected],
            "candidates": [c.to_dict() for c in self.candidates],
        }


#: Owner-name similarity at or above this is a confident parcel match.
STRONG_MATCH = 0.9
#: Between this and STRONG_MATCH the parcel is a candidate needing a human.
WEAK_MATCH = 0.7


def select_parcels(
    candidates: list[ParcelRecord],
    owner: str | None,
    *,
    landowner: str | None = None,
    reported_acres: float | None = None,
) -> ParcelSelection:
    """Choose the parcels belonging to an application's owner.

    ``owner`` is the permittee from the use report; ``landowner`` is the
    property owner where the county supplied one separately.  Either may be the
    name recorded by the assessor, so both are tried and the better match wins.
    """
    if not candidates:
        return ParcelSelection(
            outcome=ParcelOutcome.NO_CANDIDATES,
            detail="no parcels intersect this application's sections",
            review_reason="no_parcel_candidates",
        )

    if not owner and not landowner:
        return ParcelSelection(
            outcome=ParcelOutcome.NEEDS_REVIEW,
            candidates=[
                ScoredParcel(parcel, 0.0, False, "no owner name to match against")
                for parcel in candidates
            ],
            detail="the use report named no operator, so parcels cannot be matched by owner",
            review_reason="missing_owner",
        )

    scored: list[ScoredParcel] = []
    for parcel in candidates:
        best_score = 0.0
        best_reason = "parcel owner does not resemble the reported operator"
        for name in (landowner, owner):
            if not name:
                continue
            match = compare_companies(name, parcel.owner)
            if match.score > best_score:
                best_score = match.score
                best_reason = f"{match.reason} (compared with {name!r})"
        scored.append(
            ScoredParcel(
                parcel=parcel,
                score=best_score,
                matched=best_score >= STRONG_MATCH,
                reason=best_reason,
            )
        )

    scored.sort(key=lambda s: s.score, reverse=True)
    strong = [s for s in scored if s.score >= STRONG_MATCH]
    weak = [s for s in scored if WEAK_MATCH <= s.score < STRONG_MATCH]

    if strong:
        # Several parcels owned by the same operator inside the treated
        # sections is the normal case for forestry: a project covers a block of
        # contiguous holdings. All of them are part of the application.
        detail = (
            f"{len(strong)} parcel(s) in this application's sections are recorded to "
            f"{strong[0].parcel.owner!r}"
        )
        if reported_acres is not None:
            parcel_acres = sum(s.parcel.acreage or 0 for s in strong)
            if parcel_acres:
                detail += (
                    f"; they total {parcel_acres:,.0f} acres against "
                    f"{reported_acres:,.0f} acres reported treated"
                )
        return ParcelSelection(
            outcome=ParcelOutcome.MATCHED,
            selected=strong,
            candidates=scored,
            confidence=Confidence.HIGH if len(strong) == 1 else Confidence.HIGH,
            detail=detail,
        )

    if weak:
        return ParcelSelection(
            outcome=ParcelOutcome.NEEDS_REVIEW,
            candidates=scored[:20],
            confidence=Confidence.MEDIUM,
            detail=(
                f"{len(weak)} parcel(s) have an owner name resembling but not matching "
                "the reported operator"
            ),
            review_reason="ambiguous_parcel",
        )

    return ParcelSelection(
        outcome=ParcelOutcome.NEEDS_REVIEW,
        candidates=scored[:20],
        confidence=Confidence.LOW,
        detail=(
            "no parcel in this application's sections is recorded to the reported "
            "operator; the land may be held under a different name, or the section "
            "may be wrong"
        ),
        review_reason="no_parcel_owner_match",
    )


def association_note(selection: ParcelSelection, reported_acres: float | None) -> str:
    """The sentence shown beneath an application's map.

    This wording is load-bearing. The tracker publishes an association between
    an application and a property; asserting that the whole property was
    treated would be a claim the source records do not support.
    """
    if not selection.selected:
        return "No parcel has been matched to this application."
    apns = ", ".join(selection.apns[:6])
    more = f" and {len(selection.apns) - 6} more" if len(selection.apns) > 6 else ""
    base = (
        f"The outlined parcels ({apns}{more}) are recorded to the operator named on "
        "this application and lie within the sections it reports."
    )
    if reported_acres:
        return (
            base
            + f" The reports describe {reported_acres:,.0f} treated acres; the outline "
            "shows the property boundaries, not the area actually sprayed."
        )
    return base + " The outline shows property boundaries, not the area actually sprayed."
