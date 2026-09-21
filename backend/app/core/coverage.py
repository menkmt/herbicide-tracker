"""The tracker's temporal and documentary scope.

Protect Lassen's tracker covers **2020 onward**.  Earlier records exist and are
sometimes produced by agencies in response to a records request, but they are
out of scope: they are kept as source documents and never published as
applications.  Having one place that decides this stops the rule from drifting
between the importer, the CPRA sync and the public API.
"""

from __future__ import annotations

from datetime import date

#: First day the tracker publishes applications for.  Anything earlier is
#: retained as a source document but not published.
COVERAGE_START = date(2020, 1, 1)

#: There is no end date — the tracker runs forward indefinitely.
COVERAGE_END: date | None = None


class DocumentKind:
    """The three kinds of record the tracker collects.

    They are genuinely different things and must never be conflated on the
    public site:

    ``use_report``
        A pesticide use report (PUR).  An application that **has happened**
        and was reported to the county afterwards.

    ``notice_of_intent``
        An NOI, filed with the county at least 24 hours **before** a
        restricted-material application.  It states an *intention* to apply.
        The application may be delayed, moved or never carried out, so an NOI
        is advance warning, not evidence that spraying occurred.

    ``permit``
        A restricted-materials permit: the county's multi-year authorisation
        naming the operator, the permitted materials, and every site that may
        be treated.  It is context for applications rather than an application.

    ``enforcement``
        A notice of proposed action, decision or order. Note that a proposed
        action is an **allegation**, not a finding — see
        :mod:`app.enforcement.model`.

    ``investigation``
        A county investigation report into a complaint, which states what was
        sampled, what was found, and who the commissioner concluded to cite.
    """

    USE_REPORT = "use_report"
    NOTICE_OF_INTENT = "notice_of_intent"
    PERMIT = "permit"
    #: A county enforcement document: a notice of proposed action, decision
    #: or order against a licensee.
    ENFORCEMENT = "enforcement"
    #: A county investigation report into a complaint.
    INVESTIGATION = "investigation"

    ALL = (USE_REPORT, NOTICE_OF_INTENT, PERMIT, ENFORCEMENT, INVESTIGATION)

    #: How each kind is labelled for the public.
    LABELS = {
        USE_REPORT: "Reported application",
        NOTICE_OF_INTENT: "Planned application (notice of intent)",
        PERMIT: "Restricted materials permit",
        ENFORCEMENT: "Enforcement action",
        INVESTIGATION: "Investigation report",
    }

    @classmethod
    def label(cls, kind: str) -> str:
        return cls.LABELS.get(kind, kind)


def in_coverage(value: date | None) -> bool:
    """True when a date falls inside the tracker's published window."""
    if value is None:
        return False
    if value < COVERAGE_START:
        return False
    return COVERAGE_END is None or value <= COVERAGE_END


def coverage_note(value: date | None) -> str | None:
    """Explain why a date is out of scope, or ``None`` when it is in scope."""
    if value is None:
        return "no date could be read, so coverage cannot be determined"
    if value < COVERAGE_START:
        return (
            f"dated {value.isoformat()}, before the tracker's coverage start of "
            f"{COVERAGE_START.isoformat()}"
        )
    if COVERAGE_END is not None and value > COVERAGE_END:
        return f"dated {value.isoformat()}, after the tracker's coverage end"
    return None
