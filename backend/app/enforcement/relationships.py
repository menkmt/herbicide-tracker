"""How companies are connected, and when a connection may be published.

A parcel's record often involves three different businesses: the landowner,
the company that manages the land, and the applicator that did the spraying.
In the real Lassen data the land is owned by RRF Lassen-Plumas LLC, managed by
W.M. Beaty & Associates, and sprayed by Western Helicopter Services — and
Western Helicopter is itself owned by a larger agricultural company.

Those connections are the point. Someone looking at a parcel wants to know who
is actually behind the application, and a corporate parent's enforcement
history is genuinely relevant context for its subsidiary's work.

They are also the easiest place in the whole tracker to say something false
about a named business, so two rules are built in:

**A relationship needs a source.**  "X is owned by Y" is a factual claim about
corporate control. It is recorded with the document or registry that
establishes it, and one without a source is never published.

**A parent's record is never presented as the subsidiary's.**  Where a related
company's enforcement history is shown on a profile, it is labelled as that
company's, with the relationship spelled out. The subsidiary's own record and
its parent's are never added together into a single count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from app.core.confidence import Confidence


class RelationshipType(StrEnum):
    #: B owns A (wholly or in controlling part).
    OWNED_BY = "owned_by"
    #: A owns B.
    OWNS = "owns"
    #: B manages A's land or operations without owning it.
    MANAGED_BY = "managed_by"
    MANAGES = "manages"
    #: A trades under the name B.
    DBA = "dba"
    #: A was acquired by B on a date.
    ACQUIRED_BY = "acquired_by"
    #: A and B share ownership or officers.
    AFFILIATE = "affiliate"
    #: B is a licensed branch of A.
    BRANCH_OF = "branch_of"

    @property
    def label(self) -> str:
        return {
            RelationshipType.OWNED_BY: "owned by",
            RelationshipType.OWNS: "owns",
            RelationshipType.MANAGED_BY: "managed by",
            RelationshipType.MANAGES: "manages",
            RelationshipType.DBA: "trading as",
            RelationshipType.ACQUIRED_BY: "acquired by",
            RelationshipType.AFFILIATE: "affiliated with",
            RelationshipType.BRANCH_OF: "a licensed branch of",
        }[self]

    @property
    def inverse(self) -> RelationshipType:
        return {
            RelationshipType.OWNED_BY: RelationshipType.OWNS,
            RelationshipType.OWNS: RelationshipType.OWNED_BY,
            RelationshipType.MANAGED_BY: RelationshipType.MANAGES,
            RelationshipType.MANAGES: RelationshipType.MANAGED_BY,
            RelationshipType.ACQUIRED_BY: RelationshipType.OWNS,
            RelationshipType.DBA: RelationshipType.DBA,
            RelationshipType.AFFILIATE: RelationshipType.AFFILIATE,
            RelationshipType.BRANCH_OF: RelationshipType.OWNS,
        }[self]

    @property
    def implies_control(self) -> bool:
        """Whether the related company's conduct is relevant context.

        Ownership and acquisition do; a management contract or a shared
        officer does not, so those do not pull in an enforcement history.
        """
        return self in {
            RelationshipType.OWNED_BY,
            RelationshipType.OWNS,
            RelationshipType.ACQUIRED_BY,
            RelationshipType.BRANCH_OF,
        }


class RelationshipSource(StrEnum):
    """Where a relationship claim comes from, strongest first."""

    #: A state business registry filing (Secretary of State, FTB).
    BUSINESS_REGISTRY = "business_registry"
    #: A county permit or licence naming one as a branch of the other.
    LICENSING_RECORD = "licensing_record"
    #: The company's own website or filings.
    COMPANY_STATEMENT = "company_statement"
    #: A news report or trade publication.
    PUBLISHED_REPORT = "published_report"
    #: Entered by an administrator from their own research.
    MANUAL = "manual"

    @property
    def confidence(self) -> str:
        return {
            RelationshipSource.BUSINESS_REGISTRY: Confidence.VERIFIED,
            RelationshipSource.LICENSING_RECORD: Confidence.VERIFIED,
            RelationshipSource.COMPANY_STATEMENT: Confidence.HIGH,
            RelationshipSource.PUBLISHED_REPORT: Confidence.MEDIUM,
            RelationshipSource.MANUAL: Confidence.MANUAL,
        }[self]


@dataclass
class CompanyRelationship:
    """One sourced connection between two businesses."""

    subject: str
    relationship: RelationshipType
    related: str
    source: RelationshipSource | None = None
    source_citation: str | None = None
    source_url: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    note: str | None = None

    @property
    def confidence(self) -> str:
        return self.source.confidence if self.source else Confidence.LOW

    @property
    def is_publishable(self) -> bool:
        """A corporate-control claim without a citation is not published."""
        return bool(self.source and self.source_citation)

    @property
    def is_current(self) -> bool:
        return self.effective_to is None

    def sentence(self) -> str:
        """How the relationship reads on a profile."""
        tense = "is" if self.is_current else "was"
        base = f"{self.subject} {tense} {self.relationship.label} {self.related}"
        if self.effective_from:
            base += f" (since {self.effective_from.year})"
        if self.effective_to:
            base += f" (until {self.effective_to.year})"
        # Many legal names already end in a full stop ("W.M. Beaty & Associates,
        # Inc."); adding another reads as a typo.
        return base if base.endswith(".") else base + "."

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "relationship": str(self.relationship),
            "relationship_label": self.relationship.label,
            "related": self.related,
            "sentence": self.sentence(),
            "implies_control": self.relationship.implies_control,
            "source": str(self.source) if self.source else None,
            "source_citation": self.source_citation,
            "source_url": self.source_url,
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "confidence": self.confidence,
            "publishable": self.is_publishable,
            "note": self.note,
        }


@dataclass
class RelatedEnforcement:
    """A related company's enforcement record, shown as theirs, not this one's."""

    company: str
    relationship: CompanyRelationship
    summary: Any  # app.enforcement.model.EnforcementSummary

    def heading(self) -> str:
        return f"Enforcement history of {self.company}"

    def explanation(self) -> str:
        """Why another company's record appears on this profile at all."""
        return (
            f"{self.relationship.sentence()} The actions below are against "
            f"{self.company}, not against {self.relationship.subject}. They are shown "
            "because corporate ownership is relevant context, and they are counted "
            "separately."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "company": self.company,
            "heading": self.heading(),
            "explanation": self.explanation(),
            "relationship": self.relationship.to_dict(),
            "enforcement": self.summary.to_dict() if self.summary else None,
        }


@dataclass
class CompanyProfileGraph:
    """The relationship block on a company card."""

    company: str
    relationships: list[CompanyRelationship] = field(default_factory=list)
    related_enforcement: list[RelatedEnforcement] = field(default_factory=list)

    @property
    def published(self) -> list[CompanyRelationship]:
        return [r for r in self.relationships if r.is_publishable]

    @property
    def controlling(self) -> list[CompanyRelationship]:
        """Relationships that justify showing another company's record."""
        return [r for r in self.published if r.relationship.implies_control]

    def summary_sentence(self) -> str | None:
        if not self.published:
            return None
        return " ".join(r.sentence() for r in self.published)

    def to_dict(self) -> dict[str, Any]:
        return {
            "company": self.company,
            "summary": self.summary_sentence(),
            "relationships": [r.to_dict() for r in self.published],
            "related_enforcement": [r.to_dict() for r in self.related_enforcement],
        }


def relationships_from_permit_contacts(
    contacts: list[dict[str, Any]], *, permit_number: str | None, operator_name: str | None
) -> list[CompanyRelationship]:
    """Derive relationships a county permit states outright.

    A permit's contact list distinguishes the grower-permittee from the pest
    control businesses working under the permit, and marks a licensed branch
    as such. Those are the county's own statements, so they are verified.
    """
    found: list[CompanyRelationship] = []
    citation = f"county restricted materials permit {permit_number}" if permit_number else None

    for contact in contacts:
        name = (contact.get("name") or "").strip()
        contact_type = (contact.get("contact_type") or "").strip()
        if not name or not contact_type:
            continue

        if "BRANCH" in contact_type.upper() and operator_name and name != operator_name:
            found.append(
                CompanyRelationship(
                    subject=name,
                    relationship=RelationshipType.BRANCH_OF,
                    related=name.split(" BRANCH")[0],
                    source=RelationshipSource.LICENSING_RECORD,
                    source_citation=citation,
                    note="listed on the permit as a pest control business branch",
                )
            )
        elif operator_name and name != operator_name and contact_type.upper() in {
            "PCM", "PCB", "PEST CONTROL BUSINESS",
        }:
            found.append(
                CompanyRelationship(
                    subject=operator_name,
                    relationship=RelationshipType.MANAGES,
                    related=name,
                    source=RelationshipSource.LICENSING_RECORD,
                    source_citation=citation,
                    note=f"{name} is authorised to apply under {operator_name}'s permit",
                )
            )
    return found
