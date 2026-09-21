"""Normalisation and comparison of the names that appear in PUR sources.

The same company is written half a dozen ways across use reports, county
permits and assessor rolls -- ``WM BEATY AND ASSOC.``, ``W.M. BEATY &
ASSOCIATES, INC.`` and ``W M Beaty & Associates`` are one business.  Matching
them is what lets the tracker attach an application to the right landowner
parcel and the right company profile.

Two rules shape everything here:

* Normalisation is lossy, so the original string is always kept alongside the
  canonical key and stored as an alias.
* A name match alone never merges two *people*.  Names are cheap coincidences;
  :func:`compare_people` reports a similarity, and the resolver requires a
  corroborating signal (licence number, employer, address) before merging.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.core.confidence import Confidence

# Legal-form words that carry no identifying information.  Dropping them lets
# "RRF Lassen-Plumas LLC" and "R.R.F. Lassen-Plumas" resolve together.
LEGAL_SUFFIXES = {
    "INC", "INCORPORATED", "LLC", "LLP", "LP", "LTD", "LIMITED", "CORP",
    "CORPORATION", "CO", "COMPANY", "PC", "PLLC", "TRUST", "THE",
}

# Words that are part of a business's identity but are written inconsistently.
# Mapping them to a single stem is what makes "ASSOC." == "ASSOCIATES".
TOKEN_STEMS = {
    "ASSOCIATES": "ASSOC",
    "ASSOCIATE": "ASSOC",
    "ASSOCS": "ASSOC",
    "ASSOCIATION": "ASSOC",
    "AND": "&",
    "SVCS": "SERVICES",
    "SVC": "SERVICES",
    "SERVICE": "SERVICES",
    "BROS": "BROTHERS",
    "MGMT": "MANAGEMENT",
    "MGT": "MANAGEMENT",
    "TIMBERLAND": "TIMBER",
    "TIMBERLANDS": "TIMBER",
    "PROPERTIES": "PROPERTY",
    "INVESTMENTS": "INVESTMENT",
    "ENTERPRISES": "ENTERPRISE",
    "FORESTRY": "FOREST",
    "HELICOPTERS": "HELICOPTER",
    "INTL": "INTERNATIONAL",
}

# Generic words that are too common to distinguish one business from another.
# They stay in the key but are excluded when testing whether one name is a
# shortened form of another.
GENERIC_TOKENS = {
    "&", "ASSOC", "SERVICES", "GROUP", "HOLDINGS", "PARTNERS", "MANAGEMENT",
    "PROPERTY", "INVESTMENT", "ENTERPRISE", "FARMS", "RANCH", "RANCHES",
    "OF", "AT",
}

PERSON_TITLES = {"MR", "MRS", "MS", "DR", "PROF"}
PERSON_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V", "ESQ", "PHD", "RPF"}

# Similarity at or above this is treated as the same organisation.
COMPANY_MATCH_THRESHOLD = 0.88
# Below this the names are considered unrelated.
COMPANY_REJECT_THRESHOLD = 0.62


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _collapse_initials(text: str) -> str:
    """``W.M.`` and ``W M`` both become ``WM``.

    Run before punctuation is stripped, because the dots are the only thing
    marking these as initials rather than separate words.
    """
    text = re.sub(r"\b([A-Z])\.\s*(?=[A-Z]\.)", r"\1", text)
    text = re.sub(r"\b([A-Z])\.(?=\s|$)", r"\1", text)
    # Now join any run of standalone single letters: "W M BEATY" -> "WM BEATY".
    return re.sub(r"\b([A-Z])\s+(?=[A-Z]\b)", r"\1", text)


def _tokenise(name: str) -> list[str]:
    text = _strip_accents(str(name)).upper().strip()
    text = text.replace("&", " & ")
    text = _collapse_initials(text)
    # Hyphens and slashes join words that other sources write with a space.
    text = re.sub(r"[-/]", " ", text)
    text = re.sub(r"[^A-Z0-9& ]", " ", text)
    tokens = [t for t in text.split() if t]
    tokens = [TOKEN_STEMS.get(t, t) for t in tokens]
    return tokens


@dataclass(frozen=True)
class NormalizedName:
    """A name reduced to a comparable key, with the original preserved."""

    original: str
    key: str
    tokens: tuple[str, ...]
    core_tokens: tuple[str, ...]

    def __bool__(self) -> bool:
        return bool(self.key)


def normalize_company(name: str | None) -> NormalizedName:
    """Reduce a business name to a canonical key.

    ``W.M. BEATY & ASSOCIATES, INC.``, ``WM BEATY AND ASSOC.`` and
    ``W M Beaty & Associates`` all reduce to ``WM BEATY & ASSOC``.
    """
    if not name or not str(name).strip():
        return NormalizedName("", "", (), ())

    original = str(name).strip()
    tokens = _tokenise(original)
    # Drop legal forms, but never let that empty the name out entirely.
    kept = [t for t in tokens if t not in LEGAL_SUFFIXES]
    if not kept:
        kept = tokens
    # A trailing conjunction left behind by a dropped suffix ("BEATY &" from
    # "Beaty & Co") carries nothing.
    while kept and kept[-1] == "&":
        kept.pop()

    core = tuple(t for t in kept if t not in GENERIC_TOKENS)
    return NormalizedName(
        original=original,
        key=" ".join(kept),
        tokens=tuple(kept),
        core_tokens=core or tuple(kept),
    )


def company_key(name: str | None) -> str:
    """Convenience wrapper returning just the canonical key."""
    return normalize_company(name).key


@dataclass(frozen=True)
class NameMatch:
    """How strongly two names refer to the same entity."""

    score: float
    matched: bool
    confidence: Confidence
    reason: str

    def __bool__(self) -> bool:
        return self.matched


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def compare_companies(a: str | None, b: str | None) -> NameMatch:
    """Compare two business names.

    Handles the shortened-form case explicitly: ``WM Beaty`` is a real
    reference to ``W.M. Beaty & Associates, Inc.``, because every distinctive
    word of the shorter name appears in the longer one.
    """
    na, nb = normalize_company(a), normalize_company(b)
    if not na or not nb:
        return NameMatch(0.0, False, Confidence.LOW, "one or both names are empty")

    if na.key == nb.key:
        return NameMatch(1.0, True, Confidence.VERIFIED, f"identical normalised name {na.key!r}")

    set_a, set_b = frozenset(na.tokens), frozenset(nb.tokens)
    if set_a == set_b:
        return NameMatch(0.97, True, Confidence.HIGH, "same words in a different order")

    core_a, core_b = frozenset(na.core_tokens), frozenset(nb.core_tokens)
    if core_a and core_b and (core_a <= core_b or core_b <= core_a):
        # One is an abbreviation of the other.  Require that they agree on the
        # first distinctive word, so "Beaty Forest" does not absorb "Beaty".
        if na.core_tokens[0] == nb.core_tokens[0]:
            return NameMatch(
                0.93,
                True,
                Confidence.HIGH,
                f"{na.key!r} and {nb.key!r} share every distinguishing word of the shorter name",
            )

    jaccard = _jaccard(set_a, set_b)
    ratio = SequenceMatcher(None, na.key, nb.key).ratio()
    score = round(0.6 * jaccard + 0.4 * ratio, 4)

    if score >= COMPANY_MATCH_THRESHOLD:
        return NameMatch(score, True, Confidence.HIGH, f"strong similarity ({score:.2f})")
    if score >= COMPANY_REJECT_THRESHOLD:
        return NameMatch(
            score,
            False,
            Confidence.MEDIUM,
            f"similar but not conclusive ({score:.2f}) — needs review",
        )
    return NameMatch(score, False, Confidence.LOW, f"names differ ({score:.2f})")


@dataclass(frozen=True)
class PersonName:
    """A person's name split into parts, however the source ordered them."""

    original: str
    given: str = ""
    middle: str = ""
    family: str = ""
    suffix: str = ""

    @property
    def key(self) -> str:
        """``COMPTON|SHANE`` — family and given name only.

        The middle name is excluded because sources include it inconsistently;
        it is used as corroboration during matching instead.
        """
        return f"{self.family}|{self.given}".strip("|")

    @property
    def display(self) -> str:
        parts = [self.given, self.middle, self.family, self.suffix]
        return " ".join(p for p in parts if p).title().replace("Llc", "LLC")


def normalize_person(name: str | None) -> PersonName:
    """Parse a personal name written either ``Shane Compton`` or ``Compton, Shane``."""
    if not name or not str(name).strip():
        return PersonName("")

    original = str(name).strip()
    text = _strip_accents(original).upper()
    text = re.sub(r"[^A-Z, ]", " ", text)

    suffix = ""
    if "," in text:
        head, _, tail = text.partition(",")
        tail_tokens = [t for t in tail.split() if t]
        # "Carnegie, Scott" is family-first; "Compton, Jr" is a suffix comma.
        if len(tail_tokens) == 1 and tail_tokens[0] in PERSON_SUFFIXES:
            suffix = tail_tokens[0]
            tokens = [t for t in head.split() if t]
        else:
            tokens = tail_tokens + [t for t in head.split() if t]
    else:
        tokens = [t for t in text.split() if t]

    tokens = [t for t in tokens if t not in PERSON_TITLES]
    trailing = [t for t in tokens if t in PERSON_SUFFIXES]
    if trailing:
        suffix = suffix or trailing[-1]
        tokens = [t for t in tokens if t not in PERSON_SUFFIXES]

    if not tokens:
        return PersonName(original, suffix=suffix)
    if len(tokens) == 1:
        return PersonName(original, family=tokens[0], suffix=suffix)

    given, *rest = tokens
    family = rest[-1]
    middle = " ".join(rest[:-1])
    return PersonName(original, given=given, middle=middle, family=family, suffix=suffix)


def compare_people(a: str | None, b: str | None) -> NameMatch:
    """Compare two personal names.

    A strong result here is a *candidate*, never a decision: the caller must
    corroborate with a licence number, employer or address before merging two
    people.  Shared surnames are common, and merging two real people is a
    factual error the tracker must not make.
    """
    pa, pb = normalize_person(a), normalize_person(b)
    if not pa.family or not pb.family:
        return NameMatch(0.0, False, Confidence.LOW, "a personal name could not be parsed")

    if pa.family != pb.family:
        ratio = SequenceMatcher(None, pa.family, pb.family).ratio()
        if ratio < 0.9:
            return NameMatch(round(ratio, 4), False, Confidence.LOW, "different surnames")

    if pa.given and pb.given and pa.given == pb.given:
        if pa.middle and pb.middle and pa.middle != pb.middle:
            return NameMatch(
                0.75,
                False,
                Confidence.MEDIUM,
                "same first and last name but different middle names — confirm before merging",
            )
        return NameMatch(
            0.95,
            True,
            Confidence.MEDIUM,
            "same first and last name — corroborate with a licence number or employer",
        )

    # One source may abbreviate the given name ("S. Carnegie" / "Scott Carnegie").
    if pa.given and pb.given and (pa.given.startswith(pb.given) or pb.given.startswith(pa.given)):
        return NameMatch(
            0.8,
            False,
            Confidence.MEDIUM,
            "surname matches and given name is an abbreviation — needs corroboration",
        )

    return NameMatch(0.5, False, Confidence.LOW, "same surname but different given names")
