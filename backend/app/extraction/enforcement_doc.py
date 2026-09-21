"""Reading county enforcement documents: NOPAs, decisions and orders.

A Notice of Proposed Action is the county agricultural commissioner's formal
statement that it intends to fine a licensee, what it alleges, and under which
regulation. These are public records and they arrive as scans, so they go
through the same OCR path as permits.

They are unusually valuable to this tracker because they are dense with facts
that tie back to records it already holds — the respondent's licence number,
the site ID treated, the dates, the products and the property owner. A real
Lassen NOPA, for instance, names Western Helicopter Services (licence 30717)
over an aerial application on site 291136 on 21-22 October 2024, all of which
the tracker can match against its own records.

There is a second, sharper reason to read them. The violation in that NOPA is
*failure to file pesticide use reports*. That means an enforcement document can
describe an application which never appears in the use-report data at all —
the tracker's only evidence that it happened. Those are surfaced explicitly
rather than being quietly merged in.

Everything extracted here is an *allegation* unless the document says it was
decided. See :mod:`app.enforcement.model` for how that is enforced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.core.confidence import Confidence
from app.core.provenance import ExtractionMethod, SourceType, file_provenance
from app.core.siteid import try_decode
from app.enforcement.model import ActionStage, ActionType, EnforcementAction, MatchBasis
from app.extraction.base import DataIssue, ExtractionResult
from app.extraction.fieldmap import parse_date, parse_float
from app.extraction.text_source import DocumentText, load_text

PROFILE_NAME = "enforcement_action"

_MONEY = r"\$\s?([\d,]+(?:\.\d{2})?)"

#: Phrases that mark the document as a proposed action rather than a decision.
_PROPOSED_MARKERS = (
    "NOTICE OF PROPOSED ACTION",
    "PROPOSES TO FINE",
    "RIGHT TO REQUEST HEARING",
)
_DECIDED_MARKERS = (
    "DECISION AND ORDER",
    "FINAL ORDER",
    "COMMISSIONER'S DECISION",
    "STIPULATION AND WAIVER",
)
_CLEARED_MARKERS = ("DISMISSED", "WITHDRAWN", "NO ACTION WILL BE TAKEN")


@dataclass
class RelatedApplication:
    """An application described inside an enforcement document.

    This is what the agency says happened, which may differ from — or exist
    entirely without — a filed use report.
    """

    site_ids: list[str] = field(default_factory=list)
    mtrs: list[str] = field(default_factory=list)
    property_owner: str | None = None
    date_start: date | None = None
    date_end: date | None = None
    products: list[dict[str, str]] = field(default_factory=list)
    method: str | None = None
    #: True when the document alleges the use reports were never filed, which
    #: means the tracker will not hold this application from any other source.
    use_reports_not_filed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "site_ids": self.site_ids,
            "mtrs": self.mtrs,
            "property_owner": self.property_owner,
            "date_start": self.date_start.isoformat() if self.date_start else None,
            "date_end": self.date_end.isoformat() if self.date_end else None,
            "products": self.products,
            "method": self.method,
            "use_reports_not_filed": self.use_reports_not_filed,
        }


@dataclass
class EnforcementDocument:
    """One parsed enforcement document."""

    action: EnforcementAction
    related: RelatedApplication = field(default_factory=RelatedApplication)
    investigation_number: str | None = None
    investigator: str | None = None
    complaint_date: date | None = None
    violation_class: str | None = None
    fine_range: tuple[float, float] | None = None
    statutory_authority: str | None = None
    commissioner: str | None = None
    respondent_address: str | None = None
    prior_history_statement: str | None = None
    issues: list[DataIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.to_dict(),
            "related_application": self.related.to_dict(),
            "investigation_number": self.investigation_number,
            "investigator": self.investigator,
            "complaint_date": self.complaint_date.isoformat() if self.complaint_date else None,
            "violation_class": self.violation_class,
            "fine_range": list(self.fine_range) if self.fine_range else None,
            "statutory_authority": self.statutory_authority,
            "commissioner": self.commissioner,
            "respondent_address": self.respondent_address,
            "prior_history_statement": self.prior_history_statement,
            "issues": [i.to_dict() for i in self.issues],
        }


#: Typographic quotes OCR produces where the document used them.
_SMART_QUOTES = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"})


def _normalise(raw: str) -> str:
    """Repair the line-level damage scanning does before anything is parsed.

    Scans break identifiers across lines — ``INV-18-20250702-\n003`` and EPA
    numbers like ``432-\n1517`` — and render quotes as typographic characters.
    Both would otherwise have to be handled by every pattern separately, and
    getting a case number or an EPA registration half-right is worse than not
    reading it at all.
    """
    text = raw.translate(_SMART_QUOTES)
    # Join a hyphenated identifier split over a line break.
    text = re.sub(r"(?<=[A-Za-z0-9])-\s*\n\s*(?=[A-Za-z0-9])", "-", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text


def _first(pattern: str, text: str, group: int = 1, flags: int = re.IGNORECASE) -> str | None:
    match = re.search(pattern, text, flags)
    if not match:
        return None
    value = (match.group(group) or "").strip()
    return value or None


def _determine_stage(upper: str) -> ActionStage:
    """Read how far the action has got from the document's own language."""
    if any(marker in upper for marker in _CLEARED_MARKERS):
        return ActionStage.DISMISSED
    if any(marker in upper for marker in _DECIDED_MARKERS) and "PROPOSES TO FINE" not in upper:
        return ActionStage.FINAL
    if any(marker in upper for marker in _PROPOSED_MARKERS):
        return ActionStage.PROPOSED
    return ActionStage.UNKNOWN


def _determine_type(upper: str) -> ActionType:
    if "NOTICE OF PROPOSED ACTION" in upper or "PROPOSES TO FINE" in upper:
        return ActionType.NOPA
    if "SUSPEN" in upper or "REVOK" in upper:
        return ActionType.LICENSE_ACTION
    if "CIVIL PENALTY" in upper:
        return ActionType.CIVIL_PENALTY
    if "WARNING" in upper:
        return ActionType.WARNING
    if "VIOLATION" in upper:
        return ActionType.VIOLATION
    return ActionType.COMPLIANCE_ACTION


def parse_enforcement_text(
    document: DocumentText,
    *,
    source_name: str,
    county: str | None = None,
    sha256: str | None = None,
) -> EnforcementDocument:
    """Parse an enforcement document's text."""
    text = _normalise(document.text)
    upper = text.upper()
    used_ocr = document.used_ocr

    action = EnforcementAction(
        action_type=_determine_type(upper),
        stage=_determine_stage(upper),
        county=county,
    )
    parsed = EnforcementDocument(action=action)

    # --- who issued it ---------------------------------------------------
    county_name = _first(r"COUNTY OF ([A-Z][A-Za-z ]+)", text)
    if county_name:
        action.county = county_name.strip().title()
    action.agency = (
        f"{action.county} County Department of Agriculture"
        if action.county
        else _first(r"(DEPARTMENT OF AGRICULTURE[^\n]*)", text)
    )
    parsed.commissioner = _first(
        r"([A-Z][a-z]+(?: [A-Z]\.?)? [A-Z][a-z]+)[^\n]{0,12}\n[^\n]{0,12}"
        r"Agriculture Commissioner",
        text,
    )

    # --- identity of the action ------------------------------------------
    action.case_number = _first(r"FILE\s*NO\.?:?\s*([A-Z0-9/\-]+)", text)
    action.action_date = parse_date(
        _first(r"\bDate:\s*([A-Z][a-z]+ \d{1,2},? \d{4})", text)
    )

    # --- respondent -------------------------------------------------------
    respondent = _first(r"\bTO:\s*([^\n]+)", text)
    if respondent:
        action.respondent_name = respondent.strip(" .,")
    # The address follows the respondent block, before the body text.
    address = _first(
        r"\bTO:\s*[^\n]+\n((?:[^\n]*\n){1,4}?)\s*(?:You are hereby|YOU ARE HEREBY)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if address:
        parsed.respondent_address = " ".join(
            line.strip() for line in address.splitlines() if line.strip()
        )
    action.respondent_license = _first(r"license number\s*([0-9]{3,8})", text)

    # --- what is alleged --------------------------------------------------
    action.regulation_cited = _first(
        r"3\s*CCR\)?\s*,?\s*section\s*([0-9]{3,5}\s*\([a-z]\)|[0-9]{3,5})", text
    )
    if action.regulation_cited:
        action.regulation_cited = "3 CCR " + re.sub(r"\s+", "", action.regulation_cited)

    parsed.statutory_authority = _first(
        r"section\s*(1\d{4}(?:\.\d)?)\s*of the\s*Food and Agricultural Code", text
    )
    if parsed.statutory_authority:
        parsed.statutory_authority = f"FAC {parsed.statutory_authority}"

    # The sentence beginning "proposes to fine" carries the whole allegation.
    allegation = _first(
        r"proposes to fine\s+(.{20,600}?)(?:\.\s|\n\s*\n)", text, flags=re.IGNORECASE | re.DOTALL
    )
    if allegation:
        action.allegation = re.sub(r"\s+", " ", allegation).strip()

    penalty = _first(r"civil penalty of\s*" + _MONEY, text) or _first(
        r"for a total of\s*" + _MONEY, text
    )
    if penalty:
        amount = parse_float(penalty)
        if action.stage is ActionStage.FINAL:
            action.final_penalty_usd = amount
        else:
            action.proposed_penalty_usd = amount

    parsed.violation_class = _first(r'["\u201c]?\s*Class\s*([A-C])\s*["\u201d]?\s*violation', text)
    fine_range = re.search(
        r"fine range for a\s*[\"\u201c]?\s*Class\s*([A-C])\s*[\"\u201d]?\s*violation is\s*"
        + _MONEY
        + r"\s*to\s*"
        + _MONEY,
        text,
        re.IGNORECASE,
    )
    if fine_range:
        # The class stated alongside its own fine range is the authoritative
        # one; a bare "Class A" elsewhere may belong to an enclosure.
        parsed.violation_class = fine_range.group(1).upper()
        low, high = parse_float(fine_range.group(2)), parse_float(fine_range.group(3))
        if low is not None and high is not None:
            parsed.fine_range = (low, high)

    parsed.investigation_number = _first(r"\b(INV-[A-Z0-9\-]+)", text)
    parsed.investigator = _first(
        r"(?:Agricultural Biologist[^,]*|[Ii]nspector),?\s*([A-Z][a-z]+ [A-Z][a-z]+)", text
    )
    parsed.complaint_date = parse_date(
        _first(r"On\s+([A-Z][a-z]+ \d{1,2},? \d{4})\s+Agricultural Biologist", text)
    )
    parsed.prior_history_statement = _first(
        r"((?:has no past history|has no record of|has a (?:prior|past) history)[^.]*\.)", text
    )

    # --- the application the action is about ------------------------------
    related = parsed.related
    for site in re.findall(r"site number\s*([0-9]{6,8})", text, re.IGNORECASE):
        if site not in related.site_ids:
            related.site_ids.append(site)
            decoded = try_decode(site, county=action.county)
            if decoded:
                related.mtrs.append(decoded.mtrs)

    # "applied ... to WM Beaty & Associates property, site number 291136".
    # Anchored on the site number so the generic wording inside the quoted
    # regulation ("to the operator of the property") cannot match instead.
    # A business name may contain a bare ampersand ("WM Beaty & Associates"),
    # which is a word in the name but has no initial capital of its own.
    _owner_word = r"(?:[A-Z][\w.'\-]*|&)"
    related.property_owner = _first(
        rf"to\s+((?:{_owner_word}\s+){{1,6}}?)property,?\s*site number", text
    ) or _first(rf"applied\s+(?:\w+\s+){{0,4}}?to\s+((?:{_owner_word}\s+){{1,6}}?)property", text)
    if related.property_owner:
        related.property_owner = related.property_owner.strip(" .,")

    span = re.search(
        r"on\s+([A-Z][a-z]+)\s+(\d{1,2})\s*[-–]\s*(\d{1,2}),?\s*(\d{4})", text
    )
    if span:
        month, first_day, last_day, year = span.groups()
        related.date_start = parse_date(f"{month} {first_day}, {year}")
        related.date_end = parse_date(f"{month} {last_day}, {year}")
    else:
        single = _first(
            r"applications? (?:performed|completed|made) in\s+([A-Z][a-z]+ \d{4})", text
        )
        if single:
            related.date_start = parse_date(f"{single.split()[0]} 1, {single.split()[1]}")

    for name, epa in re.findall(
        r"([A-Z][A-Za-z0-9 .'\-]{2,40}?),?\s*EPA number\s*([0-9]{2,6}-[0-9]{1,6})", text
    ):
        related.products.append({"name": name.strip(" .,"), "epa_reg_no": epa})

    if re.search(r"aerial|helicopter|aircraft", text, re.IGNORECASE):
        related.method = "aerial"

    related.use_reports_not_filed = bool(
        re.search(
            r"(?:use reports? (?:were|was) not filed|not submitting a[^.]*job report|"
            r"did not (?:turn in|submit|file)[^.]*(?:report))",
            text,
            re.IGNORECASE,
        )
    )

    # --- identity matching -------------------------------------------------
    if action.respondent_license:
        action.match_basis = MatchBasis.LICENSE_NUMBER
        action.match_confidence = Confidence.VERIFIED if not used_ocr else Confidence.HIGH
    elif action.respondent_name:
        action.match_basis = MatchBasis.NAME_SIMILARITY
        action.match_confidence = Confidence.MEDIUM
        parsed.issues.append(
            DataIssue(
                "enforcement_weak_match",
                f"the document names {action.respondent_name!r} but gives no licence "
                "number, so it cannot be attached to a business without a check",
            )
        )

    if action.stage is ActionStage.UNKNOWN:
        parsed.issues.append(
            DataIssue(
                "enforcement_unknown_stage",
                "the document does not say whether this action was upheld, so it is "
                "recorded with its outcome unknown",
                severity="note",
            )
        )
    if used_ocr:
        parsed.issues.append(
            DataIssue(
                "low_ocr_confidence",
                "this enforcement document was read by OCR from a scan; check the "
                "penalty, dates and respondent before publishing",
            )
        )

    action.source_document = source_name
    action.notes.append(
        f"read from {source_name}"
        + (" by OCR" if used_ocr else "")
    )
    return parsed


def extract(
    path: str | Path,
    *,
    county: str | None = None,
    sha256: str | None = None,
    source_name: str | None = None,
    allow_ocr: bool = True,
) -> ExtractionResult:
    path = Path(path)
    name = source_name or path.name
    document = load_text(path, allow_ocr=allow_ocr)
    result = ExtractionResult(source_name=name, sha256=sha256, profile=PROFILE_NAME)
    result.notes.extend(document.notes)
    result.document_text = document.text

    if not document.text.strip():
        result.issues.append(DataIssue("empty_document", "no text could be read from this file"))
        return result

    parsed = parse_enforcement_text(
        document, source_name=name, county=county, sha256=sha256
    )
    # Enforcement documents are carried on the result's notes and issues; the
    # pipeline picks them up from `enforcement` below.
    result.enforcement = [parsed]  # type: ignore[attr-defined]
    result.notes.append(
        f"parsed a {parsed.action.action_type.label.lower()} against "
        f"{parsed.action.respondent_name or 'an unnamed respondent'}"
    )
    result.issues.extend(parsed.issues)

    file_provenance(
        source_type=SourceType.COUNTY_PERMIT,
        file_name=name,
        sha256=sha256,
        method=ExtractionMethod.OCR if document.used_ocr else ExtractionMethod.PDF_TEXT_LAYER,
    )
    return result


def sniff_text(text: str) -> float:
    """Confidence (0-1) that this text is a county enforcement document."""
    upper = text.upper()
    score = 0.0
    if "NOTICE OF PROPOSED ACTION" in upper:
        score += 0.7
    if "NATURE OF VIOLATION" in upper:
        score += 0.15
    if "AGRICULTURAL CIVIL PENALTY" in upper or "CIVIL PENALTY" in upper:
        score += 0.1
    if "RIGHT TO REQUEST HEARING" in upper:
        score += 0.1
    if "DECISION AND ORDER" in upper:
        score += 0.4
    return min(score, 1.0)
