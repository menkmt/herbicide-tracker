"""Enforcement history: violations, NOPAs and penalties.

County agricultural commissioners enforce pesticide law, and their enforcement
records are public. Attaching them to an applicator's or operator's profile is
the single most useful thing this tracker can do for someone deciding whether
a company can be trusted near their land.

It is also the part of the tracker most capable of doing real harm if it is
wrong, so three rules are built into the types rather than left to whoever
writes the display code:

**A proposed action is an allegation, not a finding.**  A Notice of Proposed
Action states what an agency intends to do and what it alleges. It may be
withdrawn, dismissed, settled for less, or overturned. The tracker therefore
always stores and displays the *stage* an action has reached, and the wording
for a proposed action never asserts that a violation occurred.

**An outcome can reverse the allegation.**  Where a NOPA was dismissed or
withdrawn, that is the headline fact about it — not a footnote. A dismissed
action is published as dismissed or not at all, never as a violation.

**A record is attached to a business only on a strong identifier.**  Two
companies can share a name; a licence number does not. An enforcement action
whose respondent cannot be matched on a licence number, or an exact registered
name, goes to review rather than onto somebody's profile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from app.core.confidence import Confidence


class ActionType(StrEnum):
    """What kind of enforcement record this is."""

    #: Notice of Proposed Action — the agency proposes a penalty. An allegation.
    NOPA = "notice_of_proposed_action"
    #: A recorded violation following investigation.
    VIOLATION = "violation"
    #: A written warning, usually for a minor or first infraction.
    WARNING = "warning"
    #: A civil penalty that has been imposed.
    CIVIL_PENALTY = "civil_penalty"
    #: Action against a licence: suspension, revocation, conditions.
    LICENSE_ACTION = "license_action"
    #: A compliance or inspection finding short of a violation.
    COMPLIANCE_ACTION = "compliance_action"

    @property
    def is_allegation(self) -> bool:
        """True when this type asserts a proposal rather than a finding."""
        return self is ActionType.NOPA

    @property
    def label(self) -> str:
        return {
            ActionType.NOPA: "Notice of Proposed Action",
            ActionType.VIOLATION: "Violation",
            ActionType.WARNING: "Warning",
            ActionType.CIVIL_PENALTY: "Civil penalty",
            ActionType.LICENSE_ACTION: "Licence action",
            ActionType.COMPLIANCE_ACTION: "Compliance action",
        }[self]


class ActionStage(StrEnum):
    """How far the action has got, which decides how it may be described."""

    PROPOSED = "proposed"
    CONTESTED = "contested"
    FINAL = "final"
    SETTLED = "settled"
    DISMISSED = "dismissed"
    WITHDRAWN = "withdrawn"
    OVERTURNED = "overturned"
    UNKNOWN = "unknown"

    @property
    def is_resolved_against(self) -> bool:
        """True only where the action was upheld or accepted."""
        return self in {ActionStage.FINAL, ActionStage.SETTLED}

    @property
    def is_cleared(self) -> bool:
        """True where the respondent was cleared, wholly or in effect."""
        return self in {
            ActionStage.DISMISSED,
            ActionStage.WITHDRAWN,
            ActionStage.OVERTURNED,
        }

    @property
    def label(self) -> str:
        return {
            ActionStage.PROPOSED: "Proposed — not decided",
            ActionStage.CONTESTED: "Contested by the respondent",
            ActionStage.FINAL: "Final",
            ActionStage.SETTLED: "Settled",
            ActionStage.DISMISSED: "Dismissed",
            ActionStage.WITHDRAWN: "Withdrawn by the agency",
            ActionStage.OVERTURNED: "Overturned on appeal",
            ActionStage.UNKNOWN: "Outcome not known",
        }[self]


class MatchBasis(StrEnum):
    """How an action was tied to a business or person."""

    LICENSE_NUMBER = "license_number"
    EXACT_REGISTERED_NAME = "exact_registered_name"
    NAME_SIMILARITY = "name_similarity"
    MANUAL = "manual"

    @property
    def is_strong(self) -> bool:
        """Only a licence number, an exact name, or a person's decision."""
        return self in {
            MatchBasis.LICENSE_NUMBER,
            MatchBasis.EXACT_REGISTERED_NAME,
            MatchBasis.MANUAL,
        }


@dataclass
class EnforcementAction:
    """One enforcement record against a named business or person."""

    action_type: ActionType
    stage: ActionStage = ActionStage.UNKNOWN
    #: The agency's own case or docket number.
    case_number: str | None = None
    agency: str | None = None
    county: str | None = None
    respondent_name: str | None = None
    respondent_license: str | None = None
    #: Date the action was issued or the violation occurred.
    action_date: date | None = None
    resolved_date: date | None = None
    #: The regulation cited, e.g. "3 CCR 6614" (drift), as written by the agency.
    regulation_cited: str | None = None
    #: The agency's own description of what is alleged or found.
    allegation: str | None = None
    proposed_penalty_usd: float | None = None
    final_penalty_usd: float | None = None
    #: Whether the action relates to an application this tracker holds.
    related_application_slug: str | None = None
    source_document: str | None = None
    source_url: str | None = None
    match_basis: MatchBasis | None = None
    match_confidence: str = Confidence.LOW
    notes: list[str] = field(default_factory=list)

    # -- publication rules -------------------------------------------------
    @property
    def is_publishable(self) -> bool:
        """Whether this may appear on a public profile.

        Requires a strong identity match and enough detail to describe the
        action accurately. Everything else goes to review; a weak match on an
        enforcement record is a factual error about a named business.
        """
        if self.match_basis is None or not self.match_basis.is_strong:
            return False
        if not self.agency or not self.action_date:
            return False
        return True

    @property
    def review_reason(self) -> str | None:
        if self.match_basis is None:
            return "enforcement_unmatched_respondent"
        if not self.match_basis.is_strong:
            return "enforcement_weak_match"
        if not self.agency:
            return "enforcement_missing_agency"
        if not self.action_date:
            return "enforcement_missing_date"
        return None

    @property
    def headline(self) -> str:
        """How the action is described on a profile.

        The wording is derived, not stored, so it cannot drift away from the
        stage the record is actually in.
        """
        if self.stage.is_cleared:
            return f"{self.action_type.label} — {self.stage.label.lower()}"
        if self.action_type.is_allegation and not self.stage.is_resolved_against:
            return f"{self.action_type.label} (alleged) — {self.stage.label.lower()}"
        if self.stage is ActionStage.UNKNOWN:
            return f"{self.action_type.label} — outcome not known"
        return f"{self.action_type.label} — {self.stage.label.lower()}"

    @property
    def qualifier(self) -> str:
        """The sentence that must accompany the action wherever it appears."""
        if self.stage.is_cleared:
            return (
                "This action did not result in a finding against the respondent. It is "
                "shown because the record is public, not as evidence of wrongdoing."
            )
        if self.action_type.is_allegation and not self.stage.is_resolved_against:
            return (
                "A Notice of Proposed Action sets out what an agency alleges and intends "
                "to do. It is not a finding that a violation occurred, and it may be "
                "withdrawn, dismissed, reduced or overturned."
            )
        if self.stage is ActionStage.UNKNOWN:
            return (
                "The outcome of this action is not recorded in the documents obtained so "
                "far, so it is not known whether it was upheld."
            )
        if self.stage is ActionStage.CONTESTED:
            return "The respondent has contested this action; it has not been decided."
        return ""

    @property
    def penalty_display(self) -> str | None:
        """Penalty, preferring the final amount where one exists."""
        if self.final_penalty_usd is not None:
            return f"${self.final_penalty_usd:,.0f}"
        if self.proposed_penalty_usd is not None and not self.stage.is_cleared:
            return f"${self.proposed_penalty_usd:,.0f} proposed"
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": str(self.action_type),
            "action_type_label": self.action_type.label,
            "is_allegation": self.action_type.is_allegation,
            "stage": str(self.stage),
            "stage_label": self.stage.label,
            "is_cleared": self.stage.is_cleared,
            "headline": self.headline,
            "qualifier": self.qualifier,
            "case_number": self.case_number,
            "agency": self.agency,
            "county": self.county,
            "respondent_name": self.respondent_name,
            "respondent_license": self.respondent_license,
            "action_date": self.action_date.isoformat() if self.action_date else None,
            "resolved_date": self.resolved_date.isoformat() if self.resolved_date else None,
            "regulation_cited": self.regulation_cited,
            "allegation": self.allegation,
            "penalty": self.penalty_display,
            "related_application": self.related_application_slug,
            "source_document": self.source_document,
            "source_url": self.source_url,
            "match_basis": str(self.match_basis) if self.match_basis else None,
            "match_confidence": self.match_confidence,
            "publishable": self.is_publishable,
            "review_reason": self.review_reason,
            "notes": list(self.notes),
        }


@dataclass
class EnforcementSummary:
    """The enforcement block on a company or person profile."""

    actions: list[EnforcementAction] = field(default_factory=list)
    #: Set when no enforcement records have been requested or received yet.
    records_requested: bool = False
    last_checked: date | None = None

    @property
    def published(self) -> list[EnforcementAction]:
        return [a for a in self.actions if a.is_publishable]

    @property
    def upheld(self) -> list[EnforcementAction]:
        return [a for a in self.published if a.stage.is_resolved_against]

    @property
    def pending(self) -> list[EnforcementAction]:
        return [
            a
            for a in self.published
            if a.stage in {ActionStage.PROPOSED, ActionStage.CONTESTED, ActionStage.UNKNOWN}
        ]

    @property
    def cleared(self) -> list[EnforcementAction]:
        return [a for a in self.published if a.stage.is_cleared]

    def statement(self) -> str:
        """What a profile says about enforcement, including when it knows nothing.

        An empty enforcement section is ambiguous — it could mean a clean record
        or a record nobody has asked for — so it always says which.
        """
        if not self.records_requested:
            return (
                "Enforcement records for this business have not yet been requested from "
                "the relevant county agricultural commissioners. An empty section here "
                "does not mean there are none."
            )
        if not self.published:
            checked = (
                f" as of {self.last_checked.isoformat()}" if self.last_checked else ""
            )
            return (
                "No enforcement actions were found in the records obtained from the "
                f"county agricultural commissioners{checked}."
            )
        parts = []
        if self.upheld:
            parts.append(f"{len(self.upheld)} upheld")
        if self.pending:
            parts.append(f"{len(self.pending)} not yet decided")
        if self.cleared:
            parts.append(f"{len(self.cleared)} dismissed or withdrawn")
        return f"{len(self.published)} enforcement record(s): " + ", ".join(parts) + "."

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement(),
            "records_requested": self.records_requested,
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
            "counts": {
                "total": len(self.published),
                "upheld": len(self.upheld),
                "pending": len(self.pending),
                "cleared": len(self.cleared),
            },
            "actions": [a.to_dict() for a in self.published],
        }
