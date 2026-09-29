"""Reading a county's inspection log from a spreadsheet or CSV.

Counties keep inspection logs in whatever their office uses; the columns are
never named the same twice. This matches headers by meaning, so an export
titled "Insp Date" and one titled "Date of Inspection" both work, and
anything it cannot place is kept in the notes rather than dropped.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "inspected_on": ("date", "inspection date", "insp date", "date of inspection",
                     "inspected"),
    "inspection_type": ("type", "inspection type", "insp type", "activity", "form"),
    "document_number": ("document", "doc", "doc no", "document number", "report",
                        "report no", "report number", "inspection no", "inspection number",
                        "form no"),
    "site_id": ("site", "site id", "site_id", "site no", "site number"),
    "mtrs": ("mtrs", "section", "township range section", "legal"),
    "permit_number": ("permit", "permit no", "permit number", "operator id", "permit id"),
    "operator_name": ("operator", "operator name", "grower", "property operator", "owner"),
    "applicator_name": ("applicator", "applicator name", "pco", "business", "company"),
    "inspector_name": ("inspector", "inspector name", "biologist", "staff"),
    "outcome": ("outcome", "result", "compliance", "in compliance", "status", "finding"),
    "violations_count": ("violations", "violation count", "no of violations", "# violations",
                         "violations found"),
    "notes": ("notes", "comments", "remarks", "description"),
}

TYPE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("use monitor", "use_monitoring"),
    ("application", "use_monitoring"),
    ("pum", "use_monitoring"),
    ("mix", "mix_load"),
    ("load", "mix_load"),
    ("record", "records"),
    ("headquarter", "headquarters"),
    ("hq", "headquarters"),
    ("field worker", "field_worker"),
    ("fieldworker", "field_worker"),
    ("worker", "field_worker"),
)

DATE_FORMATS = (
    "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%d-%b-%Y", "%b %d, %Y", "%B %d, %Y",
)


@dataclass
class InspectionRow:
    inspected_on: date | None
    inspection_type: str
    inspection_type_raw: str | None
    document_number: str | None
    site_id: str | None
    mtrs: str | None
    permit_number: str | None
    operator_name: str | None
    applicator_name: str | None
    inspector_name: str | None
    outcome: str
    violations_count: int | None
    notes: str | None
    #: Columns that matched no known field, kept so nothing is lost.
    extra: dict[str, str] = field(default_factory=dict)
    line: int = 0


@dataclass
class CsvResult:
    rows: list[InspectionRow]
    columns_matched: dict[str, str]
    columns_unmatched: list[str]
    problems: list[str]


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9#]+", " ", header.strip().lower()).strip()


def match_headers(headers: list[str]) -> tuple[dict[str, str], list[str]]:
    """Map our field names to the file's column names."""
    mapping: dict[str, str] = {}
    unmatched: list[str] = []
    for header in headers:
        key = _norm(header)
        for fld, aliases in HEADER_ALIASES.items():
            if fld in mapping:
                continue
            if key == fld.replace("_", " ") or key in aliases:
                mapping[fld] = header
                break
        else:
            unmatched.append(header)
    return mapping, unmatched


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def classify_type(value: str | None) -> str:
    if not value:
        return "other"
    key = value.lower()
    for needle, kind in TYPE_KEYWORDS:
        if needle in key:
            return kind
    return "other"


def classify_outcome(value: str | None, violations: int | None) -> str:
    if violations:
        return "violation"
    if not value:
        return "not_stated"
    key = value.lower()
    if any(word in key for word in ("violation", "non-compl", "noncompl", "nopa", "fail", "no ")):
        return "violation"
    if any(word in key for word in ("compl", "pass", "ok", "yes", "none")):
        return "in_compliance"
    return "not_stated"


def parse_int(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"\d+", value)
    return int(m.group()) if m else None


def read_inspections(content: bytes | str) -> CsvResult:
    text = content.decode("utf-8-sig", errors="replace") if isinstance(content, bytes) else content
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [h for h in (reader.fieldnames or []) if h]
    mapping, unmatched = match_headers(headers)
    problems: list[str] = []
    if "inspected_on" not in mapping:
        problems.append("no column could be read as the inspection date")

    rows: list[InspectionRow] = []
    for number, raw in enumerate(reader, start=2):
        def get(fld: str, raw: dict = raw) -> str | None:
            if fld not in mapping:
                return None
            return (raw.get(mapping[fld]) or "").strip() or None

        if not any((raw.get(h) or "").strip() for h in headers):
            continue
        violations = parse_int(get("violations_count"))
        inspected_on = parse_date(get("inspected_on"))
        if get("inspected_on") and inspected_on is None:
            problems.append(f"line {number}: could not read the date {get('inspected_on')!r}")
        extra = {h: (raw.get(h) or "").strip() for h in unmatched if (raw.get(h) or "").strip()}
        rows.append(
            InspectionRow(
                inspected_on=inspected_on,
                inspection_type=classify_type(get("inspection_type")),
                inspection_type_raw=get("inspection_type"),
                document_number=get("document_number"),
                site_id=(get("site_id") or "").replace(" ", "") or None,
                mtrs=get("mtrs"),
                permit_number=get("permit_number"),
                operator_name=get("operator_name"),
                applicator_name=get("applicator_name"),
                inspector_name=get("inspector_name"),
                outcome=classify_outcome(get("outcome"), violations),
                violations_count=violations,
                notes=get("notes"),
                extra=extra,
                line=number,
            )
        )
    return CsvResult(
        rows=rows, columns_matched=mapping, columns_unmatched=unmatched, problems=problems
    )
