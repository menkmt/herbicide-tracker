"""Confidence vocabulary shared by every enrichment step.

Confidence is not decoration: it decides what gets published automatically and
what a human has to look at.  Only ``verified`` and ``high`` facts are eligible
for automatic publication; anything at ``medium`` or below goes to the review
queue.
"""

from __future__ import annotations

from enum import StrEnum


class Confidence(StrEnum):
    #: Read directly from an authoritative source with no inference at all.
    VERIFIED = "verified"
    #: Inferred, but by a rule strong enough to publish without review.
    HIGH = "high"
    #: Plausible but genuinely ambiguous — always routed to review.
    MEDIUM = "medium"
    #: Weak signal; recorded as a candidate, never published on its own.
    LOW = "low"
    #: Set by an administrator in the review queue; outranks everything.
    MANUAL = "manual"


#: Ordering used when reconciling two claims about the same fact.
_RANK = {
    Confidence.LOW: 0,
    Confidence.MEDIUM: 1,
    Confidence.HIGH: 2,
    Confidence.VERIFIED: 3,
    Confidence.MANUAL: 4,
}

#: Levels that may be published without a human looking at them first.
PUBLISHABLE = frozenset({Confidence.VERIFIED, Confidence.HIGH, Confidence.MANUAL})


def rank(value: str | Confidence) -> int:
    return _RANK[Confidence(value)]


def is_publishable(value: str | Confidence) -> bool:
    """True when a fact at this confidence may be auto-published."""
    return Confidence(value) in PUBLISHABLE


def weakest(*values: str | Confidence) -> Confidence:
    """The confidence of a conclusion is that of its weakest input."""
    if not values:
        raise ValueError("weakest() requires at least one confidence value")
    return min((Confidence(v) for v in values), key=rank)


def strongest(*values: str | Confidence) -> Confidence:
    if not values:
        raise ValueError("strongest() requires at least one confidence value")
    return max((Confidence(v) for v in values), key=rank)
