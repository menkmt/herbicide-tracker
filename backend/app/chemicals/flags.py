"""The chemical warning system.

Three levels, kept strictly separate because they mean different things:

``RED``
    Either a *regulatory* restriction (a California restricted material or a
    federally restricted-use pesticide) or a *Protect Lassen watchlist* entry.
    These are never merged.  Calling a watchlisted chemical "restricted" would
    be a false statement about the law, so every red flag states its own exact
    reason and the public page prints that reason, not a generic label.

``ORANGE``
    Environmental concern — groundwater protection listing, leaching potential,
    aquatic toxicity, persistence.

``YELLOW``
    Label-level hazard — signal word, PPE, buffer and aquatic-use restrictions.

A flag without a source is not displayed.  Every flag carries the document or
dataset it came from, because "this chemical is restricted" is a factual claim
about a named business's activity and has to be defensible.

One useful consequence of parsing county permits: a restricted-materials
permit's PESTICIDES LIST *is* the county's own statement of which materials
require a permit at that site.  That makes it a first-class, citable source of
California restricted-material status, independent of any external dataset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.confidence import Confidence
from app.core.provenance import Provenance


class FlagLevel:
    RED = "red"
    ORANGE = "orange"
    YELLOW = "yellow"

    ORDER = {RED: 0, ORANGE: 1, YELLOW: 2}


class FlagReason:
    """Exact reasons, so a flag never has to be explained generically."""

    CALIFORNIA_RESTRICTED = "california_restricted_material"
    FEDERAL_RESTRICTED_USE = "federal_restricted_use"
    WATCHLIST = "protect_lassen_watchlist"

    GROUNDWATER_PROTECTION = "groundwater_protection_list"
    LEACHING = "leaching_potential"
    AQUATIC_TOXICITY = "aquatic_toxicity"
    RUNOFF = "runoff_concern"
    PERSISTENCE = "persistence"

    SIGNAL_WORD_DANGER = "signal_word_danger"
    SIGNAL_WORD_WARNING = "signal_word_warning"
    PPE_REQUIRED = "ppe_requirement"
    BUFFER_REQUIRED = "buffer_requirement"
    AQUATIC_USE_RESTRICTION = "aquatic_use_restriction"

    #: Human-readable headline for each reason, as shown on the public page.
    LABELS = {
        CALIFORNIA_RESTRICTED: "RED — California Restricted Material",
        FEDERAL_RESTRICTED_USE: "RED — Federal Restricted Use Pesticide",
        # Filled from configuration at import time so a deployment run by
        # another organisation does not display Protect Lassen's name.
        WATCHLIST: "RED — {watchlist}",
        GROUNDWATER_PROTECTION: "ORANGE — Groundwater protection listing",
        LEACHING: "ORANGE — Leaching and soil mobility concern",
        AQUATIC_TOXICITY: "ORANGE — Aquatic toxicity",
        RUNOFF: "ORANGE — Surface water runoff concern",
        PERSISTENCE: "ORANGE — Environmental persistence",
        SIGNAL_WORD_DANGER: "YELLOW — Label signal word: DANGER",
        SIGNAL_WORD_WARNING: "YELLOW — Label signal word: WARNING",
        PPE_REQUIRED: "YELLOW — Personal protective equipment required",
        BUFFER_REQUIRED: "YELLOW — Application buffer required",
        AQUATIC_USE_RESTRICTION: "YELLOW — Aquatic use restriction",
    }

    LEVELS = {
        CALIFORNIA_RESTRICTED: FlagLevel.RED,
        FEDERAL_RESTRICTED_USE: FlagLevel.RED,
        WATCHLIST: FlagLevel.RED,
        GROUNDWATER_PROTECTION: FlagLevel.ORANGE,
        LEACHING: FlagLevel.ORANGE,
        AQUATIC_TOXICITY: FlagLevel.ORANGE,
        RUNOFF: FlagLevel.ORANGE,
        PERSISTENCE: FlagLevel.ORANGE,
        SIGNAL_WORD_DANGER: FlagLevel.YELLOW,
        SIGNAL_WORD_WARNING: FlagLevel.YELLOW,
        PPE_REQUIRED: FlagLevel.YELLOW,
        BUFFER_REQUIRED: FlagLevel.YELLOW,
        AQUATIC_USE_RESTRICTION: FlagLevel.YELLOW,
    }


@dataclass(frozen=True)
class ChemicalFlag:
    """One warning about one chemical, with the source that justifies it."""

    reason: str
    subject: str
    detail: str
    provenance: Provenance
    #: True for regulatory restrictions, False for Protect Lassen's own
    #: editorial watchlist.  The public page uses this to keep the two apart.
    is_regulatory: bool = True

    @property
    def level(self) -> str:
        return FlagReason.LEVELS[self.reason]

    @property
    def label(self) -> str:
        from app.branding import get_brand

        return FlagReason.LABELS[self.reason].format(
            watchlist=get_brand().watchlist_label
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "reason": self.reason,
            "label": self.label,
            "subject": self.subject,
            "detail": self.detail,
            "is_regulatory": self.is_regulatory,
            "source": self.provenance.to_dict(),
            "source_citation": self.provenance.describe(),
        }


@dataclass
class FlagSet:
    """All flags attached to a product, ingredient or application."""

    flags: list[ChemicalFlag] = field(default_factory=list)

    def add(self, flag: ChemicalFlag) -> None:
        # The same restriction can arrive from several permits; one is enough.
        key = (flag.reason, flag.subject.upper())
        if any((f.reason, f.subject.upper()) == key for f in self.flags):
            return
        self.flags.append(flag)

    def extend(self, other: FlagSet) -> None:
        for flag in other.flags:
            self.add(flag)

    @property
    def highest_level(self) -> str | None:
        if not self.flags:
            return None
        return min((f.level for f in self.flags), key=lambda level: FlagLevel.ORDER[level])

    @property
    def has_red(self) -> bool:
        return any(f.level == FlagLevel.RED for f in self.flags)

    @property
    def regulatory_red(self) -> list[ChemicalFlag]:
        return [f for f in self.flags if f.level == FlagLevel.RED and f.is_regulatory]

    @property
    def watchlist_red(self) -> list[ChemicalFlag]:
        return [f for f in self.flags if f.level == FlagLevel.RED and not f.is_regulatory]

    def by_level(self) -> dict[str, list[ChemicalFlag]]:
        grouped: dict[str, list[ChemicalFlag]] = {
            FlagLevel.RED: [], FlagLevel.ORANGE: [], FlagLevel.YELLOW: []
        }
        for flag in self.flags:
            grouped[flag.level].append(flag)
        return grouped

    def headline(self) -> str | None:
        """The single line the public grid row shows, if any.

        A regulatory restriction outranks a watchlist entry, because it is the
        stronger and more consequential statement.
        """
        for flag in self.regulatory_red:
            return flag.label
        for flag in self.watchlist_red:
            return flag.label
        highest = self.highest_level
        if highest is None:
            return None
        return next(f.label for f in self.flags if f.level == highest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "highest_level": self.highest_level,
            "headline": self.headline(),
            "has_regulatory_restriction": bool(self.regulatory_red),
            "has_watchlist_entry": bool(self.watchlist_red),
            "flags": [f.to_dict() for f in self.flags],
            "counts": {
                level: len(items) for level, items in self.by_level().items() if items
            },
        }


# ---------------------------------------------------------------------------
# Flag sources
# ---------------------------------------------------------------------------

def flags_from_permit_materials(
    materials: list[dict[str, Any]],
    provenance: Provenance,
) -> FlagSet:
    """Derive California restricted-material flags from a county permit.

    A restricted-materials permit exists precisely because the listed materials
    require one.  Each named material is therefore a California restricted
    material, stated by the county that issued the permit — a better citation
    than a third-party list.

    The placeholder row counties use for everything else ("NON-RESTRICTED
    USE") is skipped, since it asserts the opposite.
    """
    result = FlagSet()
    for material in materials:
        name = (material.get("name") or "").strip()
        if not name or name.upper().startswith("NON-RESTRICTED"):
            continue
        result.add(
            ChemicalFlag(
                reason=FlagReason.CALIFORNIA_RESTRICTED,
                subject=name,
                detail=(
                    f"{name} is listed as a restricted material on this county "
                    f"restricted-materials permit"
                    + (
                        f", permitted for {material['methods'].lower()} application"
                        if material.get("methods")
                        else ""
                    )
                    + "."
                ),
                provenance=provenance.with_confidence(Confidence.VERIFIED),
                is_regulatory=True,
            )
        )
    return result


def flags_from_watchlist(
    ingredient_names: list[str],
    watchlist: dict[str, Any],
    provenance: Provenance,
) -> FlagSet:
    """Flag active ingredients on Protect Lassen's watchlist.

    These are explicitly marked non-regulatory so the public page can say
    "Protect Lassen Watchlist" rather than implying a legal restriction.
    """
    result = FlagSet()
    entries = watchlist.get("active_ingredients") or []
    lookup: dict[str, dict[str, Any]] = {}
    for entry in entries:
        for alias in [entry.get("name"), *(entry.get("aliases") or [])]:
            if alias:
                lookup[str(alias).strip().upper()] = entry

    for name in ingredient_names:
        entry = lookup.get(str(name).strip().upper())
        if entry is None:
            continue
        result.add(
            ChemicalFlag(
                reason=FlagReason.WATCHLIST,
                subject=entry.get("name", name),
                detail=(entry.get("reason") or "").strip()
                or f"{name} is on the Protect Lassen watchlist.",
                provenance=provenance.with_confidence(Confidence.MANUAL),
                is_regulatory=False,
            )
        )
    return result


#: Signal words to their flag reasons.
_SIGNAL_WORDS = {
    "DANGER": FlagReason.SIGNAL_WORD_DANGER,
    "DANGER-POISON": FlagReason.SIGNAL_WORD_DANGER,
    "WARNING": FlagReason.SIGNAL_WORD_WARNING,
}


def flags_from_label(
    *,
    subject: str,
    signal_word: str | None,
    federal_restricted_use: bool | None,
    provenance: Provenance,
    aquatic_restriction: str | None = None,
    buffer_requirement: str | None = None,
) -> FlagSet:
    """Flags taken from a product's registered label."""
    result = FlagSet()

    if federal_restricted_use:
        result.add(
            ChemicalFlag(
                reason=FlagReason.FEDERAL_RESTRICTED_USE,
                subject=subject,
                detail=(
                    f"{subject} is classified by the U.S. EPA as a restricted use "
                    "pesticide, which may only be applied by or under the direct "
                    "supervision of a certified applicator."
                ),
                provenance=provenance,
            )
        )

    reason = _SIGNAL_WORDS.get((signal_word or "").strip().upper())
    if reason:
        result.add(
            ChemicalFlag(
                reason=reason,
                subject=subject,
                detail=f"The registered label for {subject} carries the signal word "
                f"{signal_word.strip().upper()}.",
                provenance=provenance,
            )
        )

    if aquatic_restriction:
        result.add(
            ChemicalFlag(
                reason=FlagReason.AQUATIC_USE_RESTRICTION,
                subject=subject,
                detail=aquatic_restriction,
                provenance=provenance,
            )
        )

    if buffer_requirement:
        result.add(
            ChemicalFlag(
                reason=FlagReason.BUFFER_REQUIRED,
                subject=subject,
                detail=buffer_requirement,
                provenance=provenance,
            )
        )

    return result


def flags_from_environmental_profile(
    *,
    subject: str,
    provenance: Provenance,
    groundwater_protection_list: bool = False,
    leaching_note: str | None = None,
    aquatic_toxicity_note: str | None = None,
    runoff_note: str | None = None,
    persistence_note: str | None = None,
) -> FlagSet:
    """Orange flags, each requiring an explicit sourced statement.

    Every parameter is a *note* rather than a boolean precisely because the
    public page must be able to print the agency's own finding, not the
    tracker's paraphrase of it.
    """
    result = FlagSet()
    pairs = (
        (
            FlagReason.GROUNDWATER_PROTECTION,
            (
                f"{subject} appears on California's groundwater protection list."
                if groundwater_protection_list
                else None
            ),
        ),
        (FlagReason.LEACHING, leaching_note),
        (FlagReason.AQUATIC_TOXICITY, aquatic_toxicity_note),
        (FlagReason.RUNOFF, runoff_note),
        (FlagReason.PERSISTENCE, persistence_note),
    )
    for reason, detail in pairs:
        if detail:
            result.add(
                ChemicalFlag(
                    reason=reason, subject=subject, detail=detail, provenance=provenance
                )
            )
    return result
