"""Mapping the many labels counties use onto one canonical field name.

Every county agricultural department's software labels the same fact
differently, and the labels change between software versions:

======================  ==========================================
canonical field         labels seen in real sources
======================  ==========================================
``site_id``             "Site ID", "Site Identification Number"
``operator_name``       "Permitee", "Permittee/Permit Operator"
``applicator_name``     "Applicator Name", "Commercial Applicator (if any)"
``permit_number``       "Permit #", "Operator ID/Permit Number"
======================  ==========================================

Adding support for a new county's export should usually mean adding synonyms
here, not writing a new parser.  Labels are matched after aggressive
normalisation (case, punctuation and whitespace removed), so "Appl. Method",
"APPL METHOD" and "Application Method" all collide onto one key.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

# ---------------------------------------------------------------------------
# Canonical field names
# ---------------------------------------------------------------------------

FIELD_SYNONYMS: dict[str, tuple[str, ...]] = {
    # identity
    "document_number": ("document number", "document #", "document no", "doc #", "doc number",
                        "use report number", "record id"),
    "permit_number": ("permit #", "permit number", "permit no", "operator id/permit number",
                      "operator id permit number", "permit", "operator id"),
    "operator_id": ("operator id", "operator #", "grower id"),
    "county_code": ("county no", "county number", "county code", "county cd"),
    "county_name": ("county", "county name"),
    "site_district": ("site district", "district", "county district #", "county district"),
    # parties
    "operator_name": ("permitee", "permittee", "permittee/permit operator", "permit operator",
                      "operator", "grower", "grower name", "operator name", "property operator"),
    "applicator_name": ("applicator name", "applicator", "commercial applicator (if any)",
                        "commercial applicator", "applicator business", "pest control business"),
    "applicator_license": ("applicator license #", "applicator license", "applicator license no",
                           "license #", "license number", "pcb license", "operator license"),
    "applicator_license_type": ("applicator license type", "license type", "contact type"),
    "applicator_address": ("applicator address", "applicator business address"),
    "pca_name": ("pca", "pca name", "pest control adviser", "pest control advisor",
                 "recommendation by", "written recommendation by"),
    "location_text": ("location", "site name", "location | site name", "location/site name",
                      "property", "property name", "ranch name", "field name"),
    # place
    "site_id": ("site id", "site identification number", "site identification no", "site_id",
                "site id number", "site no", "site number", "site"),
    "mtrs_text": ("mtrs", "section (mtrs)", "section mtrs", "legal description", "plss"),
    "township": ("township", "twp", "tship"),
    "township_dir": ("township direction", "tship dir", "twp dir"),
    "range": ("range", "rge", "rng"),
    "range_dir": ("range direction", "range dir", "rge dir"),
    "section": ("section", "sec"),
    "meridian": ("meridian", "base ln mer", "base line meridian", "bl mer", "mer"),
    # time
    "application_date": ("application date", "date applied", "appl date", "date of application",
                         "app date", "use date"),
    "application_time": ("application time", "time applied", "appl time", "app time"),
    "start_datetime": ("start date/time applied", "start date time applied", "start date applied",
                       "begin date/time applied", "start date", "date/time started"),
    "end_datetime": ("end date/time applied", "end date time applied", "end date applied",
                     "finish date/time applied", "end date", "date/time ended"),
    # what
    "method": ("appl. method", "appl method", "application method", "app method",
               "app method/fume code", "app method fume code", "method", "method of application"),
    "fume_code": ("fume code",),
    "commodity": ("commodity", "commodity treated", "crop", "site treated", "crop/site",
                  "commodity name", "commodity name/code"),
    "commodity_code": ("commodity code", "crop code", "site code"),
    "planted_amount": ("planted amount", "planted area", "planted area - units", "area planted"),
    "planted_units": ("planted units", "planted amount units"),
    "treated_amount": ("treated amount", "treated area", "treated area - units", "area treated",
                       "acres treated", "amount treated"),
    "treated_units": ("treated units", "treated amount units"),
    "block_id": ("block id", "block"),
    # product line
    "product_name": ("product name", "product", "pesticide name", "pesticide", "trade name",
                     "material", "material name"),
    "epa_reg_no": ("epa reg no", "epa reg #", "epa registration number", "epa registration no",
                   "reg no", "registration number", "epa/dpr reg no", "product reg no",
                   "dpr reg no"),
    "quantity": ("quantity used", "quantity", "amount used", "total used", "qty"),
    "quantity_units": ("quantity units", "unit", "units", "quantity uom", "amount units"),
    "registration_expired": ("registration expired", "reg expired"),
    # bookkeeping
    "submittal_status": ("submittal status", "sub status", "report status", "status"),
    "school_notification": ("school notify", "schoolsite notification", "school notification"),
    "entered_by": ("entered by", "data entry by"),
    "pre_plant": ("pre-plant application", "pre plant application", "preplant"),
    "source_system": ("source", "source system"),
}


def normalize_label(label: str | None) -> str:
    """Reduce a column header or form label to a comparison key."""
    if label is None:
        return ""
    text = str(label).strip().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Pre-computed lookup: normalised synonym -> canonical field.
_LOOKUP: dict[str, str] = {}
for _canonical, _synonyms in FIELD_SYNONYMS.items():
    _LOOKUP[normalize_label(_canonical)] = _canonical
    for _s in _synonyms:
        _LOOKUP.setdefault(normalize_label(_s), _canonical)


def match_label(label: str | None) -> str | None:
    """Return the canonical field for a source label, or ``None`` if unknown.

    Unknown labels are not an error: the raw value is still preserved on the
    record, and an unmapped label that turns out to matter is a one-line
    addition to :data:`FIELD_SYNONYMS`.
    """
    key = normalize_label(label)
    if not key:
        return None
    if key in _LOOKUP:
        return _LOOKUP[key]
    # Tolerate trailing qualifiers such as "Treated Area - Units (acres)".
    stripped = re.sub(r"\b(if any|optional|required)\b", "", key).strip()
    return _LOOKUP.get(stripped)


def map_headers(headers: list[str]) -> dict[int, str]:
    """Map column index -> canonical field for a tabular source.

    Duplicate labels keep their first occurrence, which is what county exports
    with repeated "Units" columns need: the first belongs to the field it
    follows.
    """
    mapping: dict[int, str] = {}
    seen: set[str] = set()
    for index, header in enumerate(headers):
        canonical = match_label(header)
        if canonical and canonical not in seen:
            mapping[index] = canonical
            seen.add(canonical)
    return mapping


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------

_DATE_FORMATS = (
    "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%d-%b-%Y", "%b %d, %Y",
    "%B %d, %Y", "%Y/%m/%d", "%m/%d/%Y %H:%M", "%m.%d.%Y",
)

_TIME_FORMATS = ("%I:%M %p", "%I:%M%p", "%H:%M", "%I %p", "%H:%M:%S")


def parse_date(value: str | date | datetime | None) -> date | None:
    """Parse the date formats county exports use, or return ``None``."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"\s+", " ", text)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # Fall back to a leading date inside a longer string.
    match = re.match(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text)
    if match:
        return parse_date(match.group(1))
    return None


def parse_time(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    text = str(value).strip().upper().replace(".", "")
    text = re.sub(r"\s+", " ", text)
    for fmt in _TIME_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.hour, parsed.minute
        except ValueError:
            continue
    return None


def parse_datetime(value: str | None, *, time_value: str | None = None) -> datetime | None:
    """Combine a date and an optional separate time column.

    Forms such as "Start Date/Time Applied: 7/19/2024 6:00 AM" carry both in
    one cell; tabular exports split them into two columns.  Both are handled.
    """
    if value is None and time_value is None:
        return None

    text = str(value).strip() if value is not None else ""
    day = parse_date(text)
    if day is None:
        return None

    hour_minute = None
    if time_value:
        hour_minute = parse_time(time_value)
    if hour_minute is None:
        # Look for a time inside the same cell.
        match = re.search(r"(\d{1,2}:\d{2}\s*(?:[AP]M)?)", text, re.IGNORECASE)
        if match:
            hour_minute = parse_time(match.group(1))

    hour, minute = hour_minute or (0, 0)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def parse_float(value: str | float | int | None) -> float | None:
    """Parse a number, tolerating thousands separators and stray units."""
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip().replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def split_amount_units(value: str | None) -> tuple[float | None, str | None]:
    """Split a combined cell such as ``"63 ACRES"`` into ``(63.0, "ACRES")``."""
    if value is None or value == "":
        return None, None
    text = str(value).strip()
    match = re.match(r"^\s*(-?[\d,]+(?:\.\d+)?)\s*([A-Za-z./ ]*)$", text)
    if not match:
        return parse_float(text), None
    amount = parse_float(match.group(1))
    units = match.group(2).strip().upper() or None
    return amount, units


def parse_bool(value: str | bool | None) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"y", "yes", "true", "1", "x"}:
        return True
    if text in {"n", "no", "false", "0", "not required", "none"}:
        return False
    return None


def split_commodity(value: str | None) -> tuple[str | None, str | None]:
    """Split ``"FOREST, TMBRLND / 30000-0"`` or ``"30000 FOREST, TMBRLND"``.

    Returns ``(name, code)``.  Counties put the code on either side, so both
    orders are handled.
    """
    if not value:
        return None, None
    text = str(value).strip()
    if "/" in text:
        name, _, code = text.rpartition("/")
        return name.strip() or None, code.strip() or None
    match = re.match(r"^\s*(\d{4,6}(?:-\d)?)\s+(.*)$", text)
    if match:
        return match.group(2).strip() or None, match.group(1)
    return text or None, None
