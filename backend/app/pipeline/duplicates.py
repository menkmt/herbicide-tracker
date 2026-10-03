"""Recognising the same use report arriving in a different file.

Counties and requesters send the same records more than once: a PDF and the
spreadsheet behind it, a transcription, last year's production again with
this year's. Byte-identical files are caught by their hash before anything
is read. This catches the rest: the same report, read out of a different
document.

Two records are the same report when they describe the same filing:

* same county, same kind (use report or notice of intent), same start date;
* the same place — PLSS section, or site ID when the section is unknown;
* and then, in order of strength:
  - both carry a document number: the numbers must match (two different
    numbers are two reports, however alike);
  - otherwise the products must match — the same registrations or product
    names, with the same quantities to within rounding — and, where both
    give one, the same treated acreage.

The first copy is kept; later copies are counted and reported, and add
nothing to the totals.
"""

from __future__ import annotations

import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.extraction.base import PurRecord
from app.models import PurProduct
from app.models import PurRecord as PurRecordRow


def _site(site_id: str | None) -> str:
    return re.sub(r"\W", "", site_id or "").upper()


def _doc(number: str | None) -> str:
    return re.sub(r"\W", "", number or "").upper().lstrip("0")


def _product_key(reg: str | None, name: str | None) -> str:
    if reg:
        return "R:" + re.sub(r"[^0-9-]", "", reg).strip("-")
    return "N:" + re.sub(r"[^A-Z0-9]", "", (name or "").upper())[:24]


def _qty(value) -> float | None:
    try:
        return round(float(value), 1) if value is not None else None
    except (TypeError, ValueError):
        return None


def _products_of_extracted(record: PurRecord) -> set[tuple[str, float | None]]:
    return {(_product_key(p.base_epa_reg_no or p.epa_reg_no, p.product_name), _qty(p.quantity))
            for p in record.products}


def _products_of_row(session: Session, row_id: int) -> set[tuple[str, float | None]]:
    return {(_product_key(p.base_epa_reg_no or p.epa_reg_no, p.product_name), _qty(p.quantity))
            for p in session.scalars(select(PurProduct).where(PurProduct.record_id == row_id))}


def _same_products(a: set, b: set) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    # A transcription sometimes drops a registration number that another copy
    # has; fall back to quantities alone when the product counts agree.
    return len(a) == len(b) and sorted(q for _, q in a) == sorted(q for _, q in b) \
        and all(q is not None for _, q in a)


def find_duplicate(session: Session, record: PurRecord, county_id: int | None,
                   *, source_file_id: int | None = None) -> PurRecordRow | None:
    """The stored record this one repeats, or None.

    Only records from *other* documents are compared: two entries inside one
    county export are two reports, however alike they look.
    """
    start, _ = record.date_range
    if start is None or (not record.mtrs and not record.site_id):
        return None
    place = []
    if record.mtrs:
        place.append(PurRecordRow.mtrs == record.mtrs)
    if record.site_id:
        place.append(PurRecordRow.site_id == record.site_id)
    candidates = session.scalars(
        select(PurRecordRow).where(
            PurRecordRow.county_id == county_id if county_id else PurRecordRow.county_id.is_(None),
            PurRecordRow.record_kind == record.record_kind,
            PurRecordRow.date_start == start,
            or_(*place),
            *([PurRecordRow.source_file_id != source_file_id] if source_file_id else []),
        )
    ).all()
    if not candidates:
        return None

    mine_doc = _doc(record.document_number)
    mine_products = _products_of_extracted(record)
    for row in candidates:
        if record.mtrs and row.mtrs and record.mtrs != row.mtrs:
            continue
        if not (record.mtrs and row.mtrs) and _site(record.site_id) != _site(row.site_id):
            continue
        theirs_doc = _doc(row.document_number)
        if mine_doc and theirs_doc:
            if mine_doc == theirs_doc:
                return row
            continue
        if not _same_products(mine_products, _products_of_row(session, row.id)):
            continue
        a, b = _qty(record.treated_amount), _qty(row.treated_amount)
        if a is not None and b is not None and abs(a - b) > 0.6:
            continue
        return row
    return None
