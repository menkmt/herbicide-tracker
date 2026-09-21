"""Reading individual "Pesticide Use Report" forms.

Some counties hand over one form per application rather than a table: a grid
of labelled boxes holding the permittee, the location, the dates and the
products.  They arrive as native PDFs, as scans, or pasted into a text file.

Two things make these harder than a spreadsheet and are handled explicitly:

**The site ID is not always a PLSS code.**  One real form carries
``Site Identification Number: TBarr`` — a name, not a location.  But the same
form supplies ``Section 27 / Township 29N / Range 11E / Meridian M`` in its own
boxes, so the record is still locatable.  An undecodable site ID is only a
problem when nothing else says where the application happened.

**Labels and values are separated by layout, not punctuation.**  In a form
grid, a row of labels is followed by a row of values, and after OCR the only
thing tying a value to its label is the horizontal position.  So a line holding
several known labels is treated as a header and the following line is split at
the same character offsets.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.confidence import Confidence
from app.core.coverage import DocumentKind
from app.core.provenance import ExtractionMethod, Provenance, SourceType, file_provenance
from app.core.siteid import SiteIdDecodeError, decode_structured, try_decode
from app.extraction.base import (
    DataIssue,
    ExtractionResult,
    IssueCode,
    ProductApplication,
    PurRecord,
    normalize_method,
)
from app.extraction.fieldmap import (
    match_label,
    normalize_label,
    parse_bool,
    parse_datetime,
    parse_float,
    split_amount_units,
    split_commodity,
)
from app.extraction.text_source import DocumentText, load_text

PROFILE_NAME = "pur_form"

#: Labels whose presence marks a document as a use-report form.
_FORM_MARKERS = (
    "PESTICIDE USE REPORT",
    "NOTICE OF INTENT",
    "SITE IDENTIFICATION NUMBER",
    "PERMITTEE/PERMIT OPERATOR",
)

#: A line holding at least this many known labels is a column header.
_MIN_LABELS_FOR_HEADER = 2


def _label_positions(line: str) -> list[tuple[int, int, str]]:
    """Find known labels in a line as ``(start, end, canonical field)``.

    Longest-first so "Treated Area - Units" wins over the bare "Units" inside
    it, which would otherwise map the same span to the wrong field.
    """
    found: list[tuple[int, int, str]] = []
    lowered = line.lower()
    candidates: list[tuple[int, int, str]] = []
    from app.extraction.fieldmap import FIELD_SYNONYMS

    for canonical, synonyms in FIELD_SYNONYMS.items():
        for text in (canonical.replace("_", " "), *synonyms):
            start = lowered.find(text.lower())
            if start >= 0:
                candidates.append((start, start + len(text), canonical))

    for start, end, canonical in sorted(candidates, key=lambda c: (c[0], -(c[1] - c[0]))):
        if any(start < e and s < end for s, e, _ in found):
            continue
        found.append((start, end, canonical))
    return sorted(found)


def _split_at_offsets(line: str, offsets: list[int]) -> list[str]:
    """Slice a value line at the column offsets taken from its header."""
    pieces: list[str] = []
    bounds = [*offsets, len(line) + 1000]
    for index in range(len(offsets)):
        start, end = bounds[index], bounds[index + 1]
        pieces.append(line[start:end].strip() if start < len(line) else "")
    return pieces


def parse_form_fields(text: str) -> dict[str, str]:
    """Extract ``canonical field -> value`` pairs from a form's text.

    Handles the three shapes these forms produce: ``Label: value`` on one line,
    a label alone with its value on the next line, and a row of labels above a
    row of values.
    """
    values: dict[str, str] = {}
    # Internal whitespace is NOT collapsed: in a form grid the column position
    # of a value is the only thing tying it to its label, so the runs of spaces
    # are the structure. Tabs are expanded so they measure the same way.
    lines = [line.expandtabs(8).rstrip() for line in text.splitlines()]

    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        positions = _label_positions(line)

        # A row of labels: read the next non-empty line positionally.
        if len(positions) >= _MIN_LABELS_FOR_HEADER:
            following = next(
                (
                    lines[j]
                    for j in range(index + 1, min(index + 3, len(lines)))
                    if lines[j].strip()
                ),
                "",
            )
            if following:
                offsets = [start for start, _, _ in positions]
                for (_, _, canonical), piece in zip(
                    positions, _split_at_offsets(following, offsets), strict=False
                ):
                    if piece and canonical not in values:
                        values[canonical] = piece
                index += 2
                continue

        # "Label: value" on one line.
        match = re.match(r"^\s*([A-Za-z][A-Za-z ./()#,'-]{2,60}?)\s*[:]\s*(.+)$", line)
        if match:
            canonical = match_label(match.group(1))
            if canonical and canonical not in values:
                values[canonical] = match.group(2).strip()
                index += 1
                continue

        # A label alone, value on the following line.
        canonical = match_label(re.sub(r"[:]\s*$", "", line).strip())
        if canonical and canonical not in values:
            following = next(
                (
                    lines[j]
                    for j in range(index + 1, min(index + 3, len(lines)))
                    if lines[j].strip()
                ),
                "",
            )
            if following and not match_label(following.strip()):
                values[canonical] = following.strip()
                index += 2
                continue

        index += 1

    return values


def _resolve_location(record: PurRecord, values: dict[str, str], county: str | None,
                      provenance: Provenance) -> None:
    """Locate the application, preferring the form's own T/R/S boxes."""
    township = values.get("township")
    range_ = values.get("range")
    section = values.get("section")

    if township and range_ and section:
        t_match = re.match(r"(\d+)\s*([NS])?", township.upper())
        r_match = re.match(r"(\d+)\s*([EW])?", range_.upper())
        try:
            record.site = decode_structured(
                township=t_match.group(1) if t_match else township,
                township_dir=t_match.group(2) if t_match else None,
                range_=r_match.group(1) if r_match else range_,
                range_dir=r_match.group(2) if r_match else None,
                section=section,
                meridian=values.get("meridian"),
                county=county,
                raw=record.site_id or f"{township}{range_}{section}",
            )
            record.field_sources["site"] = provenance.at(
                "Section / Township / Range / Meridian boxes"
            ).with_confidence(record.site.confidence)
            return
        except SiteIdDecodeError as exc:
            record.add_issue(
                IssueCode.UNPARSED_SECTION,
                f"the form's township/range/section boxes could not be read: {exc}",
                field_name="site",
            )

    decoded = try_decode(record.site_id, county=county) if record.site_id else None
    if decoded:
        record.site = decoded
        record.field_sources["site"] = provenance.at(
            f"site identification number {record.site_id}"
        ).with_confidence(decoded.confidence)
        return

    if record.site_id:
        # A non-PLSS site ID such as "TBarr" is a site *name*, which is useful
        # for identifying the property even though it is not a location.
        record.add_issue(
            IssueCode.MISSING_LOCATION,
            f"site identification number {record.site_id!r} is a site name rather than a "
            "PLSS code, and the form supplied no township/range/section",
            field_name="site",
        )
    else:
        record.add_issue(
            IssueCode.MISSING_LOCATION,
            "the form supplied neither a site identification number nor "
            "township/range/section",
            field_name="site",
        )


def parse_form_text(
    document: DocumentText,
    *,
    source_name: str,
    county: str | None = None,
    sha256: str | None = None,
) -> PurRecord:
    """Build a record from one use-report form."""
    used_ocr = document.used_ocr
    provenance = file_provenance(
        source_type=SourceType.PUR_USE_RECORD,
        file_name=source_name,
        sha256=sha256,
        method=ExtractionMethod.OCR if used_ocr else ExtractionMethod.PDF_TEXT_LAYER,
        confidence=Confidence.HIGH if used_ocr else Confidence.VERIFIED,
    )

    values = parse_form_fields(document.text)
    record = PurRecord(source_profile=PROFILE_NAME, county_name=county)
    record.raw = {"form_fields": dict(values)}

    upper = document.text.upper()
    if "NOTICE OF INTENT" in upper:
        # An NOI states an intention; it is not evidence an application
        # happened, and the public page must say so.
        record.record_kind = DocumentKind.NOTICE_OF_INTENT

    simple = (
        "document_number", "permit_number", "county_code", "operator_name",
        "applicator_name", "location_text", "site_id", "commodity",
        "submittal_status", "site_district", "pca_name",
    )
    for field_name in simple:
        value = values.get(field_name)
        if value:
            setattr(record, field_name, value)
            record.field_sources[field_name] = provenance.at(f"{field_name} box")

    if record.commodity:
        name, code = split_commodity(record.commodity)
        record.commodity, record.commodity_code = name, code

    record.start_datetime = parse_datetime(
        values.get("start_datetime") or values.get("application_date"),
        time_value=values.get("application_time"),
    )
    record.end_datetime = parse_datetime(values.get("end_datetime"))
    record.application_date = (
        record.start_datetime.date() if record.start_datetime else None
    )
    if record.application_date is None:
        record.add_issue(
            IssueCode.MISSING_DATE, "no readable application date", field_name="application_date"
        )

    record.method_raw = values.get("method")
    record.method = normalize_method(record.method_raw)

    record.planted_amount, record.planted_units = split_amount_units(values.get("planted_amount"))
    record.treated_amount, record.treated_units = split_amount_units(values.get("treated_amount"))

    _resolve_location(record, values, county, provenance)

    product = ProductApplication(
        product_name=values.get("product_name"),
        epa_reg_no=values.get("epa_reg_no"),
        quantity=parse_float(values.get("quantity")),
        quantity_units=values.get("quantity_units"),
        treated_amount=record.treated_amount,
        treated_units=record.treated_units,
        registration_expired=parse_bool(values.get("registration_expired")),
        line_number=1,
    )
    if product.product_name or product.epa_reg_no:
        record.products.append(product)
    else:
        record.add_issue(
            IssueCode.UNKNOWN_PRODUCT, "no product name or registration number on this form"
        )

    if not record.operator_name:
        record.add_issue(
            IssueCode.MISSING_OWNER, "no permittee or operator named on this form",
            field_name="operator_name",
        )
    if used_ocr:
        record.add_issue(
            IssueCode.LOW_OCR_CONFIDENCE,
            "this form was read by OCR from a scan; check the figures before publishing",
            severity="note",
        )

    record.check_coverage()
    return record


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

    if not document.text.strip():
        result.issues.append(DataIssue("empty_document", "no text could be read from this file"))
        return result

    record = parse_form_text(document, source_name=name, county=county, sha256=sha256)
    result.records.append(record)
    return result


def sniff_text(text: str) -> float:
    """Confidence (0-1) that this text is an individual use-report form."""
    upper = text.upper()
    hits = sum(1 for marker in _FORM_MARKERS if marker in upper)
    if not hits:
        return 0.0
    score = 0.35 + 0.2 * hits
    # A permit is also full of these words; its own markers outrank them.
    if "RESTRICTED MATERIALS PERMIT" in upper and "SITES LIST" in upper:
        return 0.0
    return min(score, 1.0)


def _unused(*_: object) -> None:  # pragma: no cover
    normalize_label("")
