"""Reading county restricted-materials permits.

A permit is the county's multi-year authorisation for an operator.  It is the
tracker's single richest document because it carries three tables the use
reports do not:

``CONTACT LIST``
    Every business and licence involved — applicators, the permittee, the
    authorised representative — with licence numbers and expiry dates.  This
    is what seeds company and person profiles.

``PESTICIDES LIST``
    The restricted materials the county authorised, with permitted
    application methods.

``SITES LIST``
    Every site the operator may treat, with the site ID, its MTRS and the
    permitted acreage.  This is an independent check on the site IDs in use
    reports, and the source of authoritative permitted acreage.

Permits arrive either as .docx or as a scan.  Rather than write two parsers,
everything here works on *text*: :mod:`app.extraction.text_source` produces it
from either, table cell boundaries are normalised to whitespace, and the
parsers locate rows by their content instead of their layout.  That is why the
same code reads ``280811 | | 18 | M28N08E11 | ... | 190 ACRES`` from a .docx
and ``280811 18 M28N08E11`` followed by ``FOREST, TMBRLND/ 30000-0 190 ACRES``
from an OCR'd scan.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.confidence import Confidence
from app.core.coverage import DocumentKind
from app.core.provenance import ExtractionMethod, Provenance, SourceType, file_provenance
from app.core.siteid import SiteIdDecodeError, cross_check
from app.extraction.base import (
    DataIssue,
    ExtractionResult,
    IssueCode,
    PermitContact,
    PermitRecord,
    PermitSite,
)
from app.extraction.fieldmap import parse_date, parse_float
from app.extraction.text_source import DocumentText, load_text

PROFILE_NAME = "restricted_materials_permit"

# An MTRS as printed on a permit, tolerating the letter/digit confusion OCR
# introduces (O for 0, I/l for 1) inside the numeric groups.
MTRS_PATTERN = re.compile(
    r"\b([MHS])\s?([0-9OIl]{1,3})([NS])\s?([0-9OIl]{1,3})([EW])\s?([0-9OIl]{1,3})\b"
)

PERMIT_NUMBER_PATTERN = re.compile(
    r"RESTRICTED\s+MATERIALS\s+PERMIT\s*[#:]?\s*([0-9]{2}\s?-\s?[0-9]{2}\s?-\s?[0-9]{3,})",
    re.IGNORECASE,
)

PHONE_PATTERN = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")

#: Contact-type codes counties print in the CONTACT LIST.
CONTACT_TYPES = {
    "AR": "Authorised representative",
    "PCB": "Pest control business",
    "PCM": "Pest control business (maintenance gardener)",
    "PCA": "Pest control adviser",
    "QAL": "Qualified applicator licence",
    "QAC": "Qualified applicator certificate",
    "QAL BCE": "Qualified applicator licence — category B/C/E",
    "GROWER-PERMITTEE": "Grower-permittee",
    "PEST CONTROL BUSINESS": "Pest control business",
    "PEST CONTROL BUSINESS BRANCH": "Pest control business branch",
}

#: Application forms and methods printed in the PESTICIDES LIST.
_FORMS = ("All Reg.", "All Reg", "Bait", "Liquid", "Dust", "Granular", "Gas", "Dry", "Solid")
_METHODS = ("Air/Ground", "Ground/Air", "Aircraft", "Ground", "Air")

SECTION_HEADINGS = (
    "CONTACT LIST",
    "PESTICIDES LIST",
    "SITES REQUIRING SCHOOLSITE NOTIFICATION",
    "SITES LIST",
    "OPERATION-WIDE CONDITIONS",
)


def _flatten(text: str) -> list[str]:
    """Split text into one line per table cell.

    Word is inconsistent about this: in the same permit the SITES LIST is one
    paragraph per row (cells separated by the ``|`` markers that
    :mod:`app.extraction.text_source` emits) while the CONTACT LIST is one
    paragraph per *cell*.  Treating ``|`` as a line break puts both layouts —
    and OCR'd scans, which have neither — into the same shape, and the row
    parsers reassemble from there.
    """
    lines: list[str] = []
    for raw in text.replace("|", "\n").splitlines():
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if line:
            lines.append(line)
    return lines


def _sections(lines: list[str]) -> dict[str, list[str]]:
    """Split permit text into its named sections.

    Page headers and footers repeat between sections; they are left in place
    because each row parser recognises its own rows and ignores the rest.
    """
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        upper = line.upper()
        heading = next((h for h in SECTION_HEADINGS if upper.startswith(h)), None)
        if heading:
            current = heading
            found.setdefault(current, [])
            remainder = line[len(heading) :].strip()
            if remainder:
                found[current].append(remainder)
            continue
        if current:
            found[current].append(line)
    return found


def _first(pattern: str, text: str, flags: int = re.IGNORECASE) -> str | None:
    match = re.search(pattern, text, flags)
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

def parse_header(text: str, permit: PermitRecord, provenance: Provenance) -> None:
    """Pull the permit's identity block out of the first page."""
    number = _first(PERMIT_NUMBER_PATTERN.pattern, text)
    if number:
        permit.permit_number = re.sub(r"\s+", "", number)
        permit.field_sources["permit_number"] = provenance.at("permit header")
        # 18-24-4500033 is district-year-operator.
        parts = permit.permit_number.split("-")
        if len(parts) == 3:
            permit.county_district = parts[0]
            permit.operator_id = parts[2]

    county = _first(r"([A-Z][A-Za-z ]+?)\s+County\s+Department\s+of\s+Agriculture", text)
    if county:
        permit.county_name = county.strip()
        permit.field_sources["county_name"] = provenance.at("county letterhead")

    operator = _first(r"Operator:\s*([^\n#]+?)(?:\s*#|\s*\n|$)", text)
    if operator:
        # Some layouts run the mailing address onto the same line.
        operator = re.split(
            r"\s+\d+\s+[A-Z][a-z]+\s+(?:Street|St|Ave|Avenue|Road|Rd|Drive|Dr|Lane|Ln|Way|Blvd)",
            operator,
        )[0]
        permit.operator_name = operator.strip(" .,")
        permit.field_sources["operator_name"] = provenance.at("permit header")

    agent = _first(r"Agent:\s*([^\n]+?)(?:\s{2,}|\s*Issued on|\s*Fall River|\s*\n|$)", text)
    if agent:
        permit.agent_name = agent.strip(" .,")
        permit.field_sources["agent_name"] = provenance.at("permit header")

    district = _first(r"County\s+District\s*#?:?\s*(\d{1,3})", text)
    if district:
        permit.county_district = district

    permit.issued_on = parse_date(_first(r"Issued on:?\s*([\d/\-]+)", text))
    permit.valid_from = parse_date(_first(r"Valid as of:?\s*([\d/\-]+)", text))
    permit.expires_on = parse_date(_first(r"Expires on:?\s*([\d/\-]+)", text))
    for name, value in (
        ("issued_on", permit.issued_on),
        ("valid_from", permit.valid_from),
        ("expires_on", permit.expires_on),
    ):
        if value:
            permit.field_sources[name] = provenance.at("permit header")

    permit.permit_duration = _first(r"Permit Duration:?\s*([A-Za-z\- ]+?)(?:\s{2,}|\n|$)", text)
    permit.type_of_use = _first(r"Type of Use:?\s*([A-Za-z ]+?)(?:\s{2,}|Notices|\n|$)", text)
    permit.primary_phone = _first(r"Primary Phone:?\s*(" + PHONE_PATTERN.pattern + ")", text)

    applicant = _first(r"Applicant:\s*([^\n]+)", text)
    if applicant:
        # "Shane Compton Staff Forester WM Beaty & Associates" — the name is
        # the leading proper-noun run, the rest is title and employer.
        cleaned = re.sub(r"^[_\s,]+", "", applicant).strip()
        match = re.match(r"([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)\s*(.*)$", cleaned)
        if match:
            permit.applicant_name = match.group(1).strip()
            title = re.split(
                r"Applicant Signature|Issuing Officer|\bDate:", match.group(2)
            )[0]
            permit.applicant_title = title.strip(" .,:") or None
        else:
            permit.applicant_name = cleaned
        permit.field_sources["applicant_name"] = provenance.at("applicant signature block")


# ---------------------------------------------------------------------------
# CONTACT LIST
# ---------------------------------------------------------------------------

_CONTACT_ROW = re.compile(
    r"^(?P<name>.+?)\s+"
    r"(?P<phone>\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})\s+"
    r"(?P<license>\d{3,9})\b"
    r"(?P<rest>.*)$"
)

_CONTACT_HEADERS = {
    "name", "auth rep phone", "auth rep. phone", "license", "expiration",
    "contact type", "phone",
}

_DATE_ONLY = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")
_LICENSE_ONLY = re.compile(r"^\d{3,9}$")


def _parse_contact_cells(lines: list[str]) -> list[PermitContact]:
    """Read a CONTACT LIST stored one cell per line.

    The columns arrive in order — name, phone, licence, expiry, type — but any
    of them may be blank and therefore absent.  Rather than assume a fixed
    stride, a phone number opens a contact and each following cell is assigned
    by what it looks like; the next name-like cell starts the next contact.
    """
    contacts: list[PermitContact] = []
    pending_name: str | None = None
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current and current.get("name"):
            contacts.append(
                PermitContact(
                    name=current["name"].strip(" .,"),
                    phone=current.get("phone"),
                    license_number=current.get("license"),
                    license_expiration=current.get("expiration"),
                    contact_type=current.get("contact_type"),
                    is_business="," not in current["name"],
                )
            )
        current = None

    for line in lines:
        key = re.sub(r"[^a-z. ]", "", line.lower()).strip()
        if key in _CONTACT_HEADERS:
            continue
        if line.upper().startswith(("PESTICIDES LIST", "SITES ", "RESTRICTED MATERIALS")):
            break
        if PHONE_PATTERN.fullmatch(line.strip()):
            flush()
            current = {"name": pending_name, "phone": line.strip()}
            pending_name = None
            continue
        if current is not None:
            if _LICENSE_ONLY.match(line) and not current.get("license"):
                current["license"] = line
                continue
            if _DATE_ONLY.match(line) and not current.get("expiration"):
                current["expiration"] = parse_date(line)
                continue
            if not current.get("contact_type") and len(line) <= 40:
                current["contact_type"] = line.strip()
                continue
        flush()
        pending_name = line.strip()
    flush()
    return contacts


def parse_contacts(lines: list[str]) -> list[PermitContact]:
    """Parse the CONTACT LIST table.

    OCR wraps long business names onto a second line ("WESTERN HELICOPTER" /
    "SERVICES"), so a short all-caps line that follows a contact and contains
    no digits is treated as a continuation of that contact's name.
    """
    # Row-per-line layouts (scans, and .docx files that keep a row together)
    # are matched directly; anything else is a cell-per-line table.
    if not any(_CONTACT_ROW.match(line) for line in lines):
        return _parse_contact_cells(lines)

    contacts: list[PermitContact] = []
    for line in lines:
        if line.upper().startswith(("NAME", "AUTH REP", "PESTICIDES LIST")):
            continue
        match = _CONTACT_ROW.match(line)
        if not match:
            if (
                contacts
                and len(line) < 40
                and not any(ch.isdigit() for ch in line)
                and line.upper() == line
                and not line.startswith("RESTRICTED")
            ):
                previous = contacts[-1]
                contacts[-1] = PermitContact(
                    name=f"{previous.name} {line}".strip(),
                    phone=previous.phone,
                    license_number=previous.license_number,
                    license_expiration=previous.license_expiration,
                    contact_type=previous.contact_type,
                    is_business=previous.is_business,
                )
            continue

        rest = match.group("rest")
        # OCR leaves artefacts such as "= =©" between the licence and the date.
        expiration = parse_date(_first(r"(\d{1,2}/\d{1,2}/\d{2,4})", rest))
        tail = re.sub(r"\d{1,2}/\d{1,2}/\d{2,4}", " ", rest)
        tail = re.sub(r"[^A-Za-z\- ]", " ", tail)
        contact_type = re.sub(r"\s+", " ", tail).strip() or None

        name = match.group("name").strip(" .,")
        contacts.append(
            PermitContact(
                name=name,
                phone=re.sub(r"\s+", " ", match.group("phone")).strip(),
                license_number=match.group("license"),
                license_expiration=expiration,
                contact_type=contact_type,
                is_business="," not in name or name.upper() == name,
            )
        )
    return contacts


# ---------------------------------------------------------------------------
# PESTICIDES LIST
# ---------------------------------------------------------------------------

_MATERIAL_HEADERS = {"number", "pesticide", "pests", "forms", "methods", "applicators"}


def _parse_material_cells(lines: list[str]) -> list[dict]:
    """Read a PESTICIDES LIST stored one cell per line.

    The table has six fixed columns and no blank cells in practice, so once the
    header cells are dropped the remaining cells can be taken six at a time.
    """
    cells = [
        line
        for line in lines
        if line.lower().strip(" .") not in _MATERIAL_HEADERS
        and not line.upper().startswith(("SITES ", "RESTRICTED MATERIALS", "PAGE "))
    ]
    materials: list[dict] = []
    index = 0
    while index < len(cells):
        if not re.match(r"^\d{3,4}$", cells[index]):
            index += 1
            continue
        row = cells[index : index + 6]
        if len(row) < 6:
            break
        materials.append(
            {
                "number": row[0],
                "name": row[1].strip(" .,") or None,
                "pests": row[2] or None,
                "form": row[3] or None,
                "methods": row[4] or None,
                "applicators": row[5] or None,
            }
        )
        index += 6
    return materials


def parse_permitted_materials(lines: list[str]) -> list[dict]:
    """Parse the PESTICIDES LIST table of authorised restricted materials."""
    if not any(re.match(r"^\d{3,4}\s+\S+\s+\S", line) for line in lines):
        return _parse_material_cells(lines)

    materials: list[dict] = []
    for line in lines:
        if line.upper().startswith(("NUMBER", "PESTICIDE ", "SITES ")):
            continue
        match = re.match(r"^(\d{3,4})\s+(.+)$", line)
        if not match:
            continue
        number, remainder = match.group(1), match.group(2).strip()

        applicators = None
        applicator_match = re.search(r"\b(PCB|PCM|PCA|QAL|QAC)\b\s*$", remainder)
        if applicator_match:
            applicators = applicator_match.group(1)
            remainder = remainder[: applicator_match.start()].strip()

        methods = None
        for candidate in _METHODS:
            if remainder.upper().endswith(candidate.upper()):
                methods = candidate
                remainder = remainder[: -len(candidate)].strip()
                break

        form = None
        for candidate in _FORMS:
            if remainder.upper().endswith(candidate.upper()):
                form = candidate
                remainder = remainder[: -len(candidate)].strip()
                break

        pests = None
        pest_match = re.search(r"\b(RODENTS|WEEDS|VARIOUS|INSECTS|DISEASE[S]?)\b\s*$", remainder)
        if pest_match:
            pests = pest_match.group(1)
            remainder = remainder[: pest_match.start()].strip()

        materials.append(
            {
                "number": number,
                "name": remainder.strip(" .,") or None,
                "pests": pests,
                "form": form,
                "methods": methods,
                "applicators": applicators,
            }
        )
    return materials


# ---------------------------------------------------------------------------
# SITES LIST
# ---------------------------------------------------------------------------

_ACREAGE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(ACRES?|SQ ?FT|EACH)", re.IGNORECASE)
_PESTICIDE_REF = re.compile(r"(\d{3,4})\s*\(([^)]+)\)")


#: A line that opens a new site entry: the site ID, alone in its own cell or
#: leading a whole row.
_SITE_ID_START = re.compile(r"^(\d{5,10})\b")


def _site_blocks(lines: list[str]) -> list[list[str]]:
    """Group SITES LIST lines into one block per site.

    A site entry spans one line (a .docx row), several lines (a .docx stored
    cell-per-paragraph) or two to three lines (an OCR'd scan).  The site ID
    opens the entry wherever it survived; when OCR lost it, a second MTRS
    inside the current block is taken as the start of the next site.
    """
    blocks: list[list[str]] = []
    current: list[str] = []

    def has_mtrs(block: list[str]) -> bool:
        return any(MTRS_PATTERN.search(line) for line in block)

    for line in lines:
        if line.upper().startswith(
            ("SITE ", "OPERATION-WIDE", "LOCATION ", "COMMODITY", "DISTRICT")
        ):
            continue
        # Page headers repeat inside the table on scans.
        if "RESTRICTED MATERIALS PERMIT" in line.upper() or line.upper().startswith("PAGE "):
            continue
        starts_new = bool(_SITE_ID_START.match(line)) or (
            MTRS_PATTERN.search(line) and has_mtrs(current)
        )
        if starts_new and current:
            blocks.append(current)
            current = []
        current.append(line)
    if current:
        blocks.append(current)
    return [b for b in blocks if has_mtrs(b)]


def parse_sites(lines: list[str], *, county: str | None) -> list[PermitSite]:
    """Parse the SITES LIST into permitted sites with decoded locations."""
    sites: list[PermitSite] = []
    for block in _site_blocks(lines):
        joined = " ".join(block)

        mtrs_match = MTRS_PATTERN.search(joined)
        mtrs_text = mtrs_match.group(0).replace(" ", "") if mtrs_match else None

        # Everything before the MTRS is the site ID, the district and,
        # sometimes, a site name — in whichever order the layout put them.
        before = joined[: mtrs_match.start()] if mtrs_match else joined
        site_id = None
        district = None
        id_match = _SITE_ID_START.match(before.strip())
        if id_match:
            site_id = id_match.group(1)
            before = before.strip()[id_match.end() :]
        district_match = re.search(r"\b(\d{1,3})\b\s*$", before.strip())
        if district_match:
            district = district_match.group(1)
            before = before.strip()[: district_match.start()]
        site_name = re.sub(r"[\s,]+$", "", before.strip()) or None
        # A one- or two-character fragment is OCR debris, not a site name.
        if site_name and len(site_name) < 3:
            site_name = None

        site = PermitSite(
            site_id=site_id,
            mtrs_text=mtrs_text,
            site_name=site_name,
            district=district,
        )

        acre_match = _ACREAGE.search(joined)
        if acre_match:
            site.acreage = parse_float(acre_match.group(1))
            site.acreage_units = acre_match.group(2).upper()

        commodity_match = re.search(r"([A-Z][A-Z, ]{3,})\s*/\s*(\d{4,6}(?:-\d)?)", joined)
        if commodity_match:
            site.commodity = commodity_match.group(1).strip(" ,")
            site.commodity_code = commodity_match.group(2)

        if "Pesticide" in joined:
            site.permitted_materials = [
                (number, name.strip()) for number, name in _PESTICIDE_REF.findall(joined)
            ]

        # Decode the location, using the site ID and MTRS to corroborate each
        # other. Agreement is what lets an OCR'd MTRS be trusted.
        try:
            checked = cross_check(site_id, mtrs_text, county=county)
            site.site = checked.decoded
            if not checked.agrees and checked.from_site_id and checked.from_mtrs:
                site.issues.append(
                    DataIssue(
                        IssueCode.SITE_ID_MTRS_CONFLICT, checked.detail, field_name="site"
                    )
                )
        except SiteIdDecodeError as exc:
            site.issues.append(
                DataIssue(IssueCode.UNDECODABLE_SITE_ID, str(exc), field_name="site_id")
            )

        sites.append(site)
    return sites


def _dedupe_contacts(contacts: list[PermitContact]) -> list[PermitContact]:
    seen: set[tuple] = set()
    unique: list[PermitContact] = []
    for contact in contacts:
        key = (
            (contact.name or "").upper().strip(" .,"),
            contact.license_number,
            contact.contact_type,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(contact)
    return unique


def _dedupe_materials(materials: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    unique: list[dict] = []
    for material in materials:
        key = (material.get("number"), (material.get("name") or "").upper())
        if key in seen:
            continue
        seen.add(key)
        unique.append(material)
    return unique


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def parse_permit_text(
    document: DocumentText,
    *,
    source_name: str,
    county: str | None = None,
    sha256: str | None = None,
) -> PermitRecord:
    """Parse permit text that came from either a .docx or an OCR'd scan."""
    used_ocr = document.used_ocr
    provenance = file_provenance(
        source_type=SourceType.RESTRICTED_MATERIALS_PERMIT,
        file_name=source_name,
        sha256=sha256,
        method=ExtractionMethod.OCR if used_ocr else ExtractionMethod.DOCX_TABLE,
        # A value read off a scan is a reading of a photograph of a document,
        # not of the document, so it never starts out verified.
        confidence=Confidence.HIGH if used_ocr else Confidence.VERIFIED,
    )

    permit = PermitRecord(source_profile=PROFILE_NAME, county_name=county)
    lines = _flatten(document.text)
    flat_text = "\n".join(lines)

    parse_header(flat_text, permit, provenance)
    permit.county_name = permit.county_name or county

    sections = _sections(lines)
    # Permits repeat their header tables on each page of a scan, and Word
    # sometimes stores them twice; the same licence on the same phone is the
    # same contact, not two.
    permit.contacts = _dedupe_contacts(parse_contacts(sections.get("CONTACT LIST", [])))
    permit.permitted_materials = _dedupe_materials(
        parse_permitted_materials(sections.get("PESTICIDES LIST", []))
    )
    permit.sites = parse_sites(sections.get("SITES LIST", []), county=permit.county_name)

    if not permit.permit_number:
        permit.issues.append(
            DataIssue(
                "missing_permit_number",
                "no restricted materials permit number could be read from this document",
            )
        )
    if not permit.sites:
        permit.issues.append(
            DataIssue(
                "no_sites_parsed",
                "no SITES LIST rows were readable, so this permit cannot corroborate "
                "any use report locations",
                severity="note",
            )
        )
    conflicted = [s for s in permit.sites if s.issues]
    if conflicted:
        permit.issues.append(
            DataIssue(
                IssueCode.SITE_ID_MTRS_CONFLICT,
                f"{len(conflicted)} of {len(permit.sites)} permitted sites had a site ID that "
                "disagrees with its printed MTRS",
            )
        )
    if used_ocr:
        permit.issues.append(
            DataIssue(
                IssueCode.LOW_OCR_CONFIDENCE,
                "this permit was read by OCR from a scan; spot-check the site list "
                "before publishing anything that depends on it",
                severity="note",
            )
        )
    return permit


def extract(
    path: str | Path,
    *,
    county: str | None = None,
    sha256: str | None = None,
    source_name: str | None = None,
    allow_ocr: bool = True,
) -> ExtractionResult:
    """Extract a restricted-materials permit from a .docx or PDF."""
    path = Path(path)
    name = source_name or path.name
    document = load_text(path, allow_ocr=allow_ocr)
    result = ExtractionResult(source_name=name, sha256=sha256, profile=PROFILE_NAME)
    result.notes.extend(document.notes)
    result.document_text = document.text

    if not document.text.strip():
        result.issues.append(
            DataIssue("empty_document", "no text could be read from this file")
        )
        return result

    permit = parse_permit_text(document, source_name=name, county=county, sha256=sha256)
    result.permits.append(permit)
    result.notes.append(
        f"parsed {len(permit.sites)} permitted site(s), {len(permit.contacts)} contact(s), "
        f"{len(permit.permitted_materials)} restricted material(s)"
    )
    return result


def sniff_text(text: str) -> float:
    """Confidence (0-1) that this text is a restricted-materials permit."""
    upper = text.upper()
    score = 0.0
    if "RESTRICTED MATERIALS PERMIT" in upper:
        score += 0.6
    if "SITES LIST" in upper:
        score += 0.2
    if "CONTACT LIST" in upper:
        score += 0.1
    if "PESTICIDES LIST" in upper:
        score += 0.1
    return min(score, 1.0)


DOCUMENT_KIND = DocumentKind.PERMIT
