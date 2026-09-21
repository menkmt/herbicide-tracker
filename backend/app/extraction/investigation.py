"""Reading county investigation reports.

When a member of the public complains about drift or runoff, the county
agricultural commissioner investigates and writes a report. These reports are
the richest documents the tracker handles: they state the parcel affected, the
treated property, the application dates, the products and their active
ingredients, any laboratory results, and the commissioner's conclusion about
who — if anyone — is to be cited.

These reports are public records, obtained from the county under the
California Public Records Act, and by default the tracker extracts them in
full — including the complainant's name and the narrative of the
investigation. A complaint and the county's response to it are the substance
of the record; a report with the complainant removed often cannot be
understood at all, since the commissioner's reasoning is a point-by-point
response to what they said.

Because a deployment may be under different legal advice, redaction is a
setting rather than a fixed behaviour: ``redact_complainant=True`` keeps the
regulatory facts and drops the complainant's identity and personal
circumstances. It is off by default.

The licensees named in a conclusion are the subject of the enforcement record
— an applicator business and a licensed pilot acting commercially — and their
licence details are public in any configuration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.core.confidence import Confidence
from app.core.siteid import parse_mtrs
from app.extraction.base import DataIssue, ExtractionResult
from app.extraction.fieldmap import parse_date
from app.extraction.text_source import DocumentText, load_text

PROFILE_NAME = "investigation_report"

#: Header labels these reports use, mapped to what we call them.
_HEADER_FIELDS = {
    "case id": "case_id",
    "subject": "subject",
    "complaint property location": "complaint_location_raw",
    "complaint date": "complaint_date",
    "site visit/sample date": "site_visit_date",
    "site visit date": "site_visit_date",
    "sample date": "site_visit_date",
    "application date": "application_dates_raw",
    "application/treated property": "treated_property_raw",
    "treated property": "treated_property_raw",
    "laboratory results": "laboratory_results",
    "conclusion": "conclusion",
}

_APN = re.compile(r"\bParcel\s*([0-9]{3}-[0-9]{3}-[0-9]{3})\b", re.IGNORECASE)
_MTRS_TOKEN = re.compile(r"\b([MHS][0-9]{1,2}[NS][0-9]{1,2}[EW][0-9]{1,2})\b")
_LICENSE = re.compile(
    r"([A-Z][A-Za-z&.,' \-]{3,60}?),?\s*"
    r"(?:License Number|Lic(?:ense)?\.?\s*(?:No\.?|#))\s*([0-9]{4,8})"
)
_PILOT = re.compile(
    r"pilot,?\s*([A-Z][a-z]+ [A-Z][a-z]+),?\s*([A-Z]{2,4})\s*License Number\s*([0-9]{4,8})"
)
_AI_FROM_PRODUCT = re.compile(
    r"([A-Z][A-Za-z0-9 .'\-]{2,40}?)\s*(?:Herbicide)?,?\s*EPA\s*#?\s*"
    r"([0-9]{2,6}-[0-9]{1,6})(?:-[A-Z]{2})?\s*with active ingredient,?\s*([A-Z][a-z]+)",
    re.IGNORECASE,
)


@dataclass
class InvestigationReport:
    """The regulatory content of a county investigation report."""

    case_id: str | None = None
    agency: str | None = None
    county: str | None = None
    commissioner: str | None = None
    subject: str | None = None
    complaint_date: date | None = None
    site_visit_date: date | None = None
    application_dates: list[date] = field(default_factory=list)
    #: Parcel and section where the complaint originated.
    complaint_apn: str | None = None
    complaint_mtrs: str | None = None
    #: Section(s) the application was made on.
    treated_mtrs: list[str] = field(default_factory=list)
    laboratory_results: str | None = None
    conclusion: str | None = None
    #: Businesses and licensees the conclusion names, with licence numbers.
    licensees: list[dict[str, str]] = field(default_factory=list)
    #: Product-to-active-ingredient facts stated by the agency. These are
    #: authoritative: the commissioner is citing the registered label.
    product_ingredients: list[dict[str, str]] = field(default_factory=list)
    #: True when the conclusion clears a named party.
    cleared_parties: list[str] = field(default_factory=list)
    issues: list[DataIssue] = field(default_factory=list)
    #: The complainant named in the report. Retained unless redaction is on.
    complainant_name: str | None = None
    #: The commissioner's numbered point-by-point findings.
    findings: list[dict[str, str]] = field(default_factory=list)
    #: True only when redaction was requested and complainant details dropped.
    complainant_withheld: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "agency": self.agency,
            "county": self.county,
            "commissioner": self.commissioner,
            "subject": self.subject,
            "complaint_date": self.complaint_date.isoformat() if self.complaint_date else None,
            "site_visit_date": (
                self.site_visit_date.isoformat() if self.site_visit_date else None
            ),
            "application_dates": [d.isoformat() for d in self.application_dates],
            "complaint_apn": self.complaint_apn,
            "complaint_mtrs": self.complaint_mtrs,
            "treated_mtrs": self.treated_mtrs,
            "laboratory_results": self.laboratory_results,
            "conclusion": self.conclusion,
            "licensees": self.licensees,
            "product_ingredients": self.product_ingredients,
            "cleared_parties": self.cleared_parties,
            "complainant_name": self.complainant_name,
            "findings": self.findings,
            "complainant_withheld": self.complainant_withheld,
            "issues": [i.to_dict() for i in self.issues],
        }


def _header_values(text: str) -> dict[str, str]:
    """Read the ``Label: value`` block at the head of the report."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\s*([A-Za-z /]{3,40}):\s*(.+?)\s*$", line)
        if not match:
            continue
        key = _HEADER_FIELDS.get(match.group(1).strip().lower())
        if key and key not in values:
            values[key] = match.group(2).strip()
    return values


def _section(text: str, heading: str, stop: tuple[str, ...]) -> str | None:
    """Pull a labelled block that runs over several lines."""
    stops = "|".join(re.escape(s) for s in stop)
    pattern = rf"{re.escape(heading)}\s*:?\s*\n?(.*?)(?=\n(?:{stops})\s*:|\Z)"
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    value = re.sub(r"\s+", " ", match.group(1)).strip()
    return value or None


def parse_investigation_text(
    document: DocumentText,
    *,
    source_name: str,
    county: str | None = None,
    redact_complainant: bool = False,
) -> InvestigationReport:
    text = document.text
    report = InvestigationReport(county=county)
    values = _header_values(text)

    report.case_id = values.get("case_id")
    report.subject = values.get("subject")
    report.complaint_date = parse_date(values.get("complaint_date"))
    report.site_visit_date = parse_date(values.get("site_visit_date"))

    county_name = re.search(r"COUNTY OF ([A-Z][A-Za-z ]+)", text)
    if county_name:
        report.county = county_name.group(1).strip().title()
    if report.county:
        report.agency = f"{report.county} County Department of Agriculture"
    commissioner = re.search(
        r"([A-Z][a-z]+ [A-Z][a-z]+)\s*\n\s*Agricultural Commissioner", text
    )
    if commissioner:
        report.commissioner = commissioner.group(1)

    # --- dates -----------------------------------------------------------
    for raw in re.findall(
        r"([A-Z][a-z]+ \d{1,2}, \d{4})", values.get("application_dates_raw", "")
    ):
        parsed = parse_date(raw)
        if parsed and parsed not in report.application_dates:
            report.application_dates.append(parsed)

    # --- geography --------------------------------------------------------
    complaint_raw = values.get("complaint_location_raw", "")
    apn = _APN.search(complaint_raw) or _APN.search(text)
    if apn:
        report.complaint_apn = apn.group(1)
    mtrs = _MTRS_TOKEN.search(complaint_raw)
    if mtrs:
        try:
            report.complaint_mtrs = parse_mtrs(mtrs.group(1)).mtrs
        except Exception:  # noqa: BLE001 - an unreadable token is not fatal
            report.complaint_mtrs = mtrs.group(1)

    for token in _MTRS_TOKEN.findall(values.get("treated_property_raw", "")):
        try:
            normalised = parse_mtrs(token).mtrs
        except Exception:  # noqa: BLE001
            normalised = token
        if normalised not in report.treated_mtrs:
            report.treated_mtrs.append(normalised)

    report.laboratory_results = values.get("laboratory_results") or _section(
        text, "Laboratory Results", ("Conclusion", "Basis for Determination")
    )
    report.conclusion = values.get("conclusion") or _section(
        text, "Conclusion", ("Basis for Determination", "Case ID")
    )

    # --- who the conclusion names -----------------------------------------
    conclusion = report.conclusion or ""
    # Matching is done sentence by sentence: a conclusion routinely clears one
    # party in one sentence and cites another in the next, and a pattern run
    # across the whole block merges the two names into one business that does
    # not exist.
    for sentence in re.split(r"(?<=\.)\s+", conclusion or text):
        for name, number in _LICENSE.findall(sentence):
            cleaned = re.sub(
                r"^(?:to|and|by|issued to)\s+", "", name.strip(" .,"), flags=re.IGNORECASE
            )
            # Drop a leading clause such as "will be issued to".
            cleaned = re.sub(r"^.*?\bto\s+", "", cleaned) if " to " in cleaned else cleaned
            entry = {"name": cleaned, "license_number": number, "role": "licensee"}
            if cleaned and entry not in report.licensees:
                report.licensees.append(entry)

    pilot = _PILOT.search(conclusion or text)
    if pilot:
        report.licensees.append(
            {
                "name": pilot.group(1),
                "license_type": pilot.group(2),
                "license_number": pilot.group(3),
                "role": "pilot",
            }
        )

    # "no violation ... will be issued to WM Beaty & Associates" clears them.
    cleared = re.search(
        r"no violation[^.]*?will be issued to\s+"
        r"((?:[A-Z][\w'\-]*|&)(?:\s+(?:[A-Z][\w'\-]*|&)){0,5})",
        conclusion or text,
    )
    if cleared:
        report.cleared_parties.append(cleared.group(1).strip(" .,"))

    # --- product chemistry, as stated by the agency -----------------------
    for product, epa, ingredient in _AI_FROM_PRODUCT.findall(text):
        # The sentence often reads "The PURs showed Velpar DF Vu Herbicide";
        # the leading verb is not part of the product name.
        product = re.sub(
            r"^(?:showed|shows|listed|lists|reported|reports|were|was|of)\s+",
            "",
            product.strip(" .,"),
            flags=re.IGNORECASE,
        )
        entry = {
            "product": product.strip(" .,"),
            "epa_reg_no": epa,
            "active_ingredient": ingredient.strip().title(),
        }
        if entry not in report.product_ingredients:
            report.product_ingredients.append(entry)

    # --- complainant and narrative ----------------------------------------
    complainant = re.search(
        r"complaint[^.]{0,80}?received from\s+([A-Z][a-z]+(?: [A-Z]\.?)? [A-Z][a-z]+)", text
    ) or re.search(r"\bcomplainant,?\s+([A-Z][a-z]+(?: [A-Z]\.?)? [A-Z][a-z]+)", text)
    if complainant and not redact_complainant:
        report.complainant_name = complainant.group(1)
    elif complainant:
        report.complainant_withheld = True
        report.issues.append(
            DataIssue(
                "complainant_details_withheld",
                "redaction is enabled for this deployment, so the complainant named in "
                "this public record was not retained",
                severity="note",
            )
        )

    # The numbered points are the commissioner's reasoning and are the
    # substance of the report; without them the conclusion is unexplained.
    if not redact_complainant:
        for number, heading, body in re.findall(
            r"\n\s*(\d{1,2})\.\s*(?:Section [^\n(]*)?\(?([^)\n]{3,80})\)?\s*\n(.+?)"
            r"(?=\n\s*\d{1,2}\.\s|\Z)",
            text,
            re.DOTALL,
        ):
            report.findings.append(
                {
                    "number": number,
                    "heading": re.sub(r"\s+", " ", heading).strip(" .,"),
                    "text": re.sub(r"\s+", " ", body).strip(),
                }
            )

    if document.used_ocr:
        report.issues.append(
            DataIssue(
                "low_ocr_confidence",
                "this report was read by OCR from a scan; check the dates, parcel and "
                "conclusion before relying on it",
            )
        )
    if not report.case_id:
        report.issues.append(
            DataIssue("missing_case_id", "no case identifier was found in this report")
        )
    return report


def extract(
    path: str | Path,
    *,
    county: str | None = None,
    sha256: str | None = None,
    source_name: str | None = None,
    allow_ocr: bool = True,
    redact_complainant: bool = False,
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

    report = parse_investigation_text(
        document, source_name=name, county=county, redact_complainant=redact_complainant
    )
    result.investigations = [report]  # type: ignore[attr-defined]
    result.notes.append(
        f"parsed investigation {report.case_id or '(no case id)'} naming "
        f"{len(report.licensees)} licensee(s)"
    )
    result.issues.extend(report.issues)
    return result


def sniff_text(text: str) -> float:
    upper = text.upper()
    score = 0.0
    if re.search(r"\bCASE ID:\s*INV-", upper):
        score += 0.6
    if "BASIS FOR DETERMINATION" in upper:
        score += 0.2
    if "COMPLAINT DATE" in upper:
        score += 0.15
    if "LABORATORY RESULTS" in upper:
        score += 0.1
    if "NOTICE OF PROPOSED ACTION" in upper and score == 0.0:
        return 0.0
    return min(score, 1.0)


CONFIDENCE_NOTE = Confidence.VERIFIED
