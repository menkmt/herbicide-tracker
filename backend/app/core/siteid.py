"""Deterministic decoder for California PUR site IDs.

California pesticide-use reports locate applications with a Public Land Survey
System (PLSS) reference encoded into a "site location ID".  Counties emit that
reference in several shapes; this module turns all of them into one normalised
Meridian-Township-Range-Section (MTRS) value while recording exactly how the
value was derived.

Nothing here guesses silently.  When a component (usually a direction letter)
is absent from the source it is filled from a county default and reported in
``inferred_fields`` with a reduced confidence, so the pipeline can route
uncertain decodes to the review queue instead of publishing them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.confidence import Confidence

# California's three PLSS principal meridians.
MERIDIANS = {
    "M": "Mount Diablo",
    "H": "Humboldt",
    "S": "San Bernardino",
}

MAX_TOWNSHIP = 48
MAX_RANGE = 60
SECTIONS_PER_TOWNSHIP = 36


class SiteIdDecodeError(ValueError):
    """Raised when a site ID cannot be decoded into a valid PLSS reference."""


@dataclass(frozen=True)
class CountyPlssDefaults:
    """Per-county fallbacks for direction letters missing from a site ID.

    PUR exports frequently omit the township/range direction because, within a
    single county, it is constant.  We keep those constants explicit and
    per-county rather than hard-coding "N/E" globally: Humboldt-meridian
    counties on the north coast run their ranges west, and getting that wrong
    would silently relocate an application by a hundred miles.
    """

    meridian: str = "M"
    township_dir: str = "N"
    range_dir: str = "E"


# Counties the tracker covers first.  Every one of these lies in the
# Mount Diablo meridian with townships north and ranges east of it.
COUNTY_DEFAULTS: dict[str, CountyPlssDefaults] = {
    "lassen": CountyPlssDefaults("M", "N", "E"),
    "plumas": CountyPlssDefaults("M", "N", "E"),
    "shasta": CountyPlssDefaults("M", "N", "E"),
    "butte": CountyPlssDefaults("M", "N", "E"),
    "tehama": CountyPlssDefaults("M", "N", "E"),
    "modoc": CountyPlssDefaults("M", "N", "E"),
    "sierra": CountyPlssDefaults("M", "N", "E"),
    "nevada": CountyPlssDefaults("M", "N", "E"),
    "placer": CountyPlssDefaults("M", "N", "E"),
    # Siskiyou straddles Mount Diablo and Humboldt; ranges east is the common
    # case but decodes there are worth reviewing when the direction is absent.
    "siskiyou": CountyPlssDefaults("M", "N", "E"),
    "trinity": CountyPlssDefaults("H", "N", "E"),
    "humboldt": CountyPlssDefaults("H", "N", "E"),
    "del norte": CountyPlssDefaults("H", "N", "E"),
    "mendocino": CountyPlssDefaults("H", "N", "W"),
}

# Counties where an omitted direction is genuinely ambiguous, so an inferred
# decode should never be treated as high confidence.
AMBIGUOUS_DIRECTION_COUNTIES = {"siskiyou", "trinity", "mendocino"}

DEFAULT_DEFAULTS = CountyPlssDefaults()


@dataclass(frozen=True)
class DecodedSiteId:
    """A site ID resolved to a PLSS section, with its derivation recorded."""

    raw: str
    township: int
    township_dir: str
    range: int
    range_dir: str
    section: int
    meridian: str = "M"
    subsection: str | None = None
    method: str = "unknown"
    confidence: str = Confidence.HIGH
    inferred_fields: tuple[str, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def mtrs(self) -> str:
        """Compact normalised key, e.g. ``M29N11E23`` — unique statewide."""
        return (
            f"{self.meridian}{self.township:02d}{self.township_dir}"
            f"{self.range:02d}{self.range_dir}{self.section:02d}"
        )

    @property
    def trs(self) -> str:
        """Township/range/section without the meridian, e.g. ``29N11E23``."""
        return self.mtrs[1:]

    @property
    def legal_description(self) -> str:
        """Human-readable legal description, e.g. ``T29N R11E Section 23``."""
        base = (
            f"T{self.township}{self.township_dir} "
            f"R{self.range}{self.range_dir} "
            f"Section {self.section}"
        )
        if self.subsection:
            base = f"{self.subsection} of {base}"
        return base

    @property
    def meridian_name(self) -> str:
        return MERIDIANS.get(self.meridian, self.meridian)

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "mtrs": self.mtrs,
            "trs": self.trs,
            "meridian": self.meridian,
            "meridian_name": self.meridian_name,
            "township": self.township,
            "township_dir": self.township_dir,
            "range": self.range,
            "range_dir": self.range_dir,
            "section": self.section,
            "subsection": self.subsection,
            "legal_description": self.legal_description,
            "method": self.method,
            "confidence": self.confidence,
            "inferred_fields": list(self.inferred_fields),
            "warnings": list(self.warnings),
        }


def _clean(raw: str) -> str:
    """Upper-case and strip the punctuation counties sprinkle into site IDs."""
    text = raw.upper().strip()
    text = re.sub(r"[.,_]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _validate(township: int, rng: int, section: int, raw: str) -> None:
    if not 1 <= section <= SECTIONS_PER_TOWNSHIP:
        raise SiteIdDecodeError(
            f"{raw!r}: section {section} is outside 1-{SECTIONS_PER_TOWNSHIP}"
        )
    if not 1 <= township <= MAX_TOWNSHIP:
        raise SiteIdDecodeError(f"{raw!r}: township {township} is outside 1-{MAX_TOWNSHIP}")
    if not 1 <= rng <= MAX_RANGE:
        raise SiteIdDecodeError(f"{raw!r}: range {rng} is outside 1-{MAX_RANGE}")


def _normalise_subsection(text: str | None) -> str | None:
    """Turn a trailing quarter-section fragment into ``NW/4`` style notation."""
    if not text:
        return None
    token = re.sub(r"[^NSEW/]", "", text.upper())
    if not token:
        return None
    parts = re.findall(r"[NS][EW]|[NSEW]", token)
    if not parts:
        return None
    return " ".join(f"{p}/4" for p in parts)


def _resolve_defaults(county: str | None, defaults: CountyPlssDefaults | None) -> CountyPlssDefaults:
    if defaults is not None:
        return defaults
    if county:
        return COUNTY_DEFAULTS.get(county.strip().lower(), DEFAULT_DEFAULTS)
    return DEFAULT_DEFAULTS


def _build(
    raw: str,
    *,
    township: int,
    rng: int,
    section: int,
    township_dir: str | None,
    range_dir: str | None,
    meridian: str | None,
    subsection: str | None,
    method: str,
    county: str | None,
    defaults: CountyPlssDefaults,
) -> DecodedSiteId:
    _validate(township, rng, section, raw)

    inferred: list[str] = []
    warnings: list[str] = []

    if township_dir is None:
        township_dir = defaults.township_dir
        inferred.append("township_dir")
    if range_dir is None:
        range_dir = defaults.range_dir
        inferred.append("range_dir")
    if meridian is None:
        meridian = defaults.meridian
        inferred.append("meridian")

    if inferred:
        source = f"{county} county defaults" if county else "statewide defaults"
        warnings.append(f"{', '.join(inferred)} not present in site ID; filled from {source}")

    # Everything present in the source itself is a verified read of the record.
    # Anything filled in from a county default is an inference, and in counties
    # that span two meridians it is not a safe one.
    if not inferred:
        confidence = Confidence.VERIFIED
    elif county and county.strip().lower() in AMBIGUOUS_DIRECTION_COUNTIES:
        confidence = Confidence.MEDIUM
        warnings.append(
            f"{county} county spans more than one PLSS meridian or range direction; "
            "confirm the decoded section before publishing"
        )
    elif county and county.strip().lower() in COUNTY_DEFAULTS:
        confidence = Confidence.HIGH
    else:
        confidence = Confidence.MEDIUM
        warnings.append("no county supplied; direction letters assumed from statewide defaults")

    return DecodedSiteId(
        raw=raw,
        township=township,
        township_dir=township_dir,
        range=rng,
        range_dir=range_dir,
        section=section,
        meridian=meridian,
        subsection=_normalise_subsection(subsection),
        method=method,
        confidence=confidence,
        inferred_fields=tuple(inferred),
        warnings=tuple(warnings),
    )


# Ordered most-specific first.  Each entry is (method name, compiled pattern).
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # T29N R12E Section 23 / 29N 11E 23 / M29N11E23 — directions spelled out.
    (
        "explicit_directional",
        re.compile(
            r"""^
            (?:(?P<meridian>[MHS])\ ?(?=\d|T))?     # optional meridian prefix
            T?\ ?(?P<township>\d{1,2})\ ?(?P<tdir>[NS])
            \ ?R?\ ?(?P<range>\d{1,2})\ ?(?P<rdir>[EW])
            \ ?(?:SECTION|SEC|S)?\ ?(?P<section>\d{1,2})
            \ ?(?P<sub>[NSEW/ ]{0,10})?
            $""",
            re.VERBOSE,
        ),
    ),
    # 29R1125 — two-digit township, a literal separator letter (R or T), then
    # two-digit range and two-digit section.
    (
        "separator_letter",
        re.compile(r"^(?P<township>\d{2})(?P<sep>[A-Z])(?P<range>\d{2})(?P<section>\d{2})$"),
    ),
    # 291223 — fully compact township/range/section.
    (
        "compact_6_digit",
        re.compile(r"^(?P<township>\d{2})(?P<range>\d{2})(?P<section>\d{2})$"),
    ),
]


def decode_site_id(
    raw: str,
    *,
    county: str | None = None,
    defaults: CountyPlssDefaults | None = None,
) -> DecodedSiteId:
    """Decode a PUR site ID into a PLSS section reference.

    ``291223`` decodes to ``T29N R12E Section 23`` for a Mount Diablo meridian
    county such as Lassen.  Raises :class:`SiteIdDecodeError` when the input
    cannot be read as a valid PLSS reference — callers should route those to
    the review queue rather than discarding the PUR record.
    """
    if raw is None or not str(raw).strip():
        raise SiteIdDecodeError("empty site ID")

    original = str(raw).strip()
    text = _clean(original)
    resolved = _resolve_defaults(county, defaults)

    for method, pattern in _PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        groups = match.groupdict()

        township_dir = groups.get("tdir")
        range_dir = groups.get("rdir")
        meridian = groups.get("meridian")

        # In the separator form the letter is usually a literal "R" for Range
        # (or "T" for Township).  When it is N or S it is the township
        # direction instead, which is real information we should keep.
        sep = groups.get("sep")
        if sep in {"N", "S"}:
            township_dir = sep
        elif sep and sep not in {"R", "T", "X", "-"}:
            # An unexpected letter means we do not actually understand the
            # format; refuse rather than mis-locate the application.
            raise SiteIdDecodeError(
                f"{original!r}: unrecognised separator {sep!r} between township and range"
            )

        return _build(
            original,
            township=int(groups["township"]),
            rng=int(groups["range"]),
            section=int(groups["section"]),
            township_dir=township_dir,
            range_dir=range_dir,
            meridian=meridian,
            subsection=groups.get("sub"),
            method=method,
            county=county,
            defaults=resolved,
        )

    raise SiteIdDecodeError(f"{original!r}: no known PUR site ID format matches")


def decode_structured(
    *,
    township: str | int,
    range_: str | int,
    section: str | int,
    township_dir: str | None = None,
    range_dir: str | None = None,
    meridian: str | None = None,
    county: str | None = None,
    raw: str | None = None,
    defaults: CountyPlssDefaults | None = None,
) -> DecodedSiteId:
    """Decode from the separate columns used by DPR's own PUR exports.

    DPR distributes PUR data with ``township``/``tship_dir``/``range``/
    ``range_dir``/``section``/``base_ln_mer`` as distinct fields.  Reading them
    directly is strictly better than re-parsing a concatenated string.
    """

    def _as_int(value: str | int, label: str) -> int:
        try:
            return int(str(value).strip().lstrip("TRS") or 0)
        except ValueError as exc:  # pragma: no cover - defensive
            raise SiteIdDecodeError(f"{label} {value!r} is not numeric") from exc

    def _letter(value: str | None, allowed: set[str]) -> str | None:
        if not value:
            return None
        letter = str(value).strip().upper()[:1]
        return letter if letter in allowed else None

    resolved = _resolve_defaults(county, defaults)
    return _build(
        raw or f"{township}{township_dir or ''}{range_}{range_dir or ''}{section}",
        township=_as_int(township, "township"),
        rng=_as_int(range_, "range"),
        section=_as_int(section, "section"),
        township_dir=_letter(township_dir, {"N", "S"}),
        range_dir=_letter(range_dir, {"E", "W"}),
        meridian=_letter(meridian, set(MERIDIANS)),
        subsection=None,
        method="structured_columns",
        county=county,
        defaults=resolved,
    )


def try_decode(raw: str, *, county: str | None = None) -> DecodedSiteId | None:
    """Best-effort decode that returns ``None`` instead of raising."""
    try:
        return decode_site_id(raw, county=county)
    except SiteIdDecodeError:
        return None


def sections_are_adjacent(a: DecodedSiteId, b: DecodedSiteId) -> bool:
    """True when two decoded sections touch or are the same section.

    Sections are numbered boustrophedonically within a township (1 at the
    north-east corner, running west, then back east on the next tier), so
    adjacency has to be computed from the grid position rather than from the
    section numbers themselves.  Sections in different townships are compared
    across the township boundary.
    """
    if a.meridian != b.meridian:
        return False

    def grid(section: int) -> tuple[int, int]:
        """Return (row from north, column from west), both 0-5."""
        row = (section - 1) // 6
        offset = (section - 1) % 6
        col = offset if row % 2 else 5 - offset
        return row, col

    def signed(value: int, direction: str, positive: str) -> int:
        return value if direction == positive else -value

    a_row, a_col = grid(a.section)
    b_row, b_col = grid(b.section)

    # Absolute grid coordinates: 6 sections per township in each direction.
    a_y = signed(a.township, a.township_dir, "N") * 6 - a_row
    b_y = signed(b.township, b.township_dir, "N") * 6 - b_row
    a_x = signed(a.range, a.range_dir, "E") * 6 + a_col
    b_x = signed(b.range, b.range_dir, "E") * 6 + b_col

    return abs(a_y - b_y) <= 1 and abs(a_x - b_x) <= 1


# ---------------------------------------------------------------------------
# MTRS strings (as printed on county restricted-materials permits)
# ---------------------------------------------------------------------------

# OCR of permit scans reliably confuses these glyphs with digits.  None of
# them is a valid meridian (M/H/S) or direction (N/S/E/W) letter, so inside an
# MTRS string they can only ever have been digits.
_OCR_DIGIT_FIXES = str.maketrans({"O": "0", "Q": "0", "D": "0", "l": "1", "I": "1", "|": "1"})

_MTRS_RE = re.compile(
    r"^(?P<meridian>[MHS])?"
    r"(?P<township>\d{1,3})(?P<tdir>[NS])"
    r"(?P<range>\d{1,3})(?P<rdir>[EW])"
    r"(?P<section>\d{1,3})$"
)


def parse_mtrs(text: str, *, repair_ocr: bool = True) -> DecodedSiteId:
    """Parse an ``M28N08E01`` style MTRS string, as printed on county permits.

    Scanned permits are the main source of these, so by default we repair the
    letter/digit confusions OCR makes.  Scans also duplicate or pad leading
    zeros (``M28NO08E01`` for ``M28N08E01``), so digit runs are read leniently
    and then validated against real PLSS bounds.  Any repair is recorded in
    ``warnings`` and drops the result to ``high`` confidence, because a
    repaired read is no longer a direct read of the source.
    """
    if text is None or not str(text).strip():
        raise SiteIdDecodeError("empty MTRS string")

    original = str(text).strip()
    candidate = re.sub(r"[\s.,_/-]", "", original.upper())

    match = _MTRS_RE.match(candidate)
    repairs: list[str] = []

    if match is None and repair_ocr:
        fixed = candidate.translate(_OCR_DIGIT_FIXES)
        match = _MTRS_RE.match(fixed)
        if match is not None:
            repairs.append(f"letter/digit confusion ({original!r} read as {fixed!r})")
            candidate = fixed

    if match is None:
        raise SiteIdDecodeError(f"{original!r}: not a recognisable MTRS string")

    groups = match.groupdict()
    # A three-character digit run means the scan duplicated or padded a zero.
    for label in ("township", "range", "section"):
        if len(groups[label]) > 2:
            repairs.append(
                f"over-long {label} field {groups[label]!r} read as {int(groups[label])}"
            )

    decoded = _build(
        original,
        township=int(groups["township"]),
        rng=int(groups["range"]),
        section=int(groups["section"]),
        township_dir=groups["tdir"],
        range_dir=groups["rdir"],
        meridian=groups["meridian"],
        subsection=None,
        method="mtrs_string",
        county=None,
        defaults=DEFAULT_DEFAULTS,
    )
    if not repairs:
        return decoded
    return DecodedSiteId(
        **{
            **decoded.__dict__,
            "confidence": Confidence.HIGH,
            "method": "mtrs_string_ocr_repaired",
            "warnings": decoded.warnings + tuple(f"OCR repair: {r}" for r in repairs),
        }
    )


@dataclass(frozen=True)
class SiteIdCrossCheck:
    """Result of checking a site ID against an independently printed MTRS."""

    agrees: bool
    decoded: DecodedSiteId
    from_site_id: DecodedSiteId | None
    from_mtrs: DecodedSiteId | None
    detail: str


def cross_check(
    site_id: str | None,
    mtrs: str | None,
    *,
    county: str | None = None,
) -> SiteIdCrossCheck:
    """Reconcile a PUR site ID with the MTRS printed beside it on a permit.

    County permits print both the packed site ID (``280801``) and the expanded
    MTRS (``M28N08E01``).  They encode the same location by different routes,
    so agreement between them is real corroboration: it confirms the decode and
    simultaneously confirms that OCR read the MTRS correctly.  Disagreement is
    never resolved silently — it is surfaced so a human can settle it.
    """
    decoded_site = try_decode(site_id, county=county) if site_id else None

    decoded_mtrs: DecodedSiteId | None = None
    if mtrs:
        try:
            decoded_mtrs = parse_mtrs(mtrs)
        except SiteIdDecodeError:
            decoded_mtrs = None

    if decoded_site and decoded_mtrs:
        if decoded_site.trs == decoded_mtrs.trs:
            # Two independent encodings agree: promote to verified and prefer
            # the MTRS reading, which carries the meridian explicitly.
            confirmed = DecodedSiteId(
                **{
                    **decoded_mtrs.__dict__,
                    "raw": str(site_id),
                    "confidence": Confidence.VERIFIED,
                    "method": "site_id_confirmed_by_mtrs",
                    "inferred_fields": (),
                    "warnings": (),
                }
            )
            return SiteIdCrossCheck(
                agrees=True,
                decoded=confirmed,
                from_site_id=decoded_site,
                from_mtrs=decoded_mtrs,
                detail=f"site ID {site_id} and MTRS {mtrs} both resolve to {confirmed.trs}",
            )
        # The two encodings disagree, so one of them is wrong and we cannot
        # tell which.  Keep the MTRS reading as the working value because it
        # is the more explicit of the two, but strip its confidence back so
        # the record is forced through review rather than published.
        disputed = DecodedSiteId(
            **{
                **decoded_mtrs.__dict__,
                "confidence": Confidence.MEDIUM,
                "method": "site_id_mtrs_conflict",
                "warnings": decoded_mtrs.warnings
                + (
                    f"conflicts with site ID {site_id} which decodes to "
                    f"{decoded_site.trs}",
                ),
            }
        )
        return SiteIdCrossCheck(
            agrees=False,
            decoded=disputed,
            from_site_id=decoded_site,
            from_mtrs=decoded_mtrs,
            detail=(
                f"site ID {site_id} decodes to {decoded_site.trs} but the permit's "
                f"MTRS {mtrs} reads {decoded_mtrs.trs}"
            ),
        )

    only = decoded_site or decoded_mtrs
    if only is None:
        raise SiteIdDecodeError(
            f"neither site ID {site_id!r} nor MTRS {mtrs!r} could be decoded"
        )
    which = "site ID" if decoded_site else "MTRS"
    if only.confidence == Confidence.VERIFIED and (site_id and mtrs):
        # Both were supplied but only one parsed — that is a data-quality
        # problem, not a corroborated read.
        only = DecodedSiteId(
            **{
                **only.__dict__,
                "confidence": Confidence.HIGH,
                "warnings": only.warnings
                + (f"the accompanying {'MTRS' if decoded_site else 'site ID'} could not be read",),
            }
        )
    return SiteIdCrossCheck(
        agrees=False,
        decoded=only,
        from_site_id=decoded_site,
        from_mtrs=decoded_mtrs,
        detail=f"only the {which} was decodable; no corroboration available",
    )
