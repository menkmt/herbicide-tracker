"""Admin: load water-monitoring stations from a spreadsheet export."""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.access import Principal
from app.db import get_session
from app.models import WaterStation
from app.reportcard.inspections_csv import parse_date

router = APIRouter(prefix="/api/admin", tags=["admin"])

COLUMNS = {
    "name": ("name", "station", "station name", "site", "site name", "system", "well"),
    "operator": ("operator", "agency", "owner", "district", "water system", "sampled by"),
    "kind": ("type", "kind", "station type", "source type"),
    "latitude": ("lat", "latitude", "y"),
    "longitude": ("lon", "lng", "long", "longitude", "x"),
    "approximate": ("approximate", "approx", "location approximate"),
    "herbicides_tested": ("herbicides tested", "herbicide tested", "tests herbicides",
                          "herbicides"),
    "analytes_note": ("analytes", "analytes tested", "tested for", "parameters"),
    "last_sampled": ("last sampled", "last sample", "sample date", "date"),
    "source_url": ("source", "source url", "url", "link"),
    "note": ("note", "notes", "comments", "finding"),
}

KINDS = {"surface": "surface_water", "stream": "surface_water", "river": "surface_water",
         "creek": "surface_water", "well": "groundwater", "ground": "groundwater",
         "drinking": "drinking_water", "public water": "drinking_water"}


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("_", " ").split())


def _yes_no(value: str | None) -> bool | None:
    if not value:
        return None
    v = value.strip().lower()
    if v in ("y", "yes", "true", "1", "tested"):
        return True
    if v in ("n", "no", "false", "0", "never", "not tested", "none"):
        return False
    return None


@router.post("/water-stations/import")
async def import_water_stations(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    _: Principal = Depends(require_admin),
) -> dict:
    text = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    mapping: dict[str, str] = {}
    for header in headers:
        key = _norm(header)
        for field, aliases in COLUMNS.items():
            if field not in mapping and (key == field.replace("_", " ") or key in aliases):
                mapping[field] = header
                break
    missing = [f for f in ("name", "latitude", "longitude") if f not in mapping]
    if missing:
        raise HTTPException(400, f"no column for: {', '.join(missing)}")

    added = updated = skipped = 0
    for raw in reader:
        def get(field: str, raw: dict = raw) -> str | None:
            return (raw.get(mapping[field]) or "").strip() or None if field in mapping else None

        try:
            lat = float(get("latitude") or "")
            lon = float(get("longitude") or "")
        except ValueError:
            skipped += 1
            continue
        if not (32 <= lat <= 42.1 and -124.6 <= lon <= -114):
            skipped += 1  # outside California
            continue
        name = get("name") or "Unnamed station"
        operator = get("operator")
        row = session.scalar(
            select(WaterStation).where(WaterStation.name == name, WaterStation.operator == operator)
        )
        if row is None:
            row = WaterStation(name=name, operator=operator)
            session.add(row)
            added += 1
        else:
            updated += 1
        kind_raw = (get("kind") or "").lower()
        row.kind = next((v for k, v in KINDS.items() if k in kind_raw), row.kind or "other")
        row.latitude, row.longitude = lat, lon
        row.location_is_approximate = bool(_yes_no(get("approximate")))
        row.herbicides_tested = _yes_no(get("herbicides_tested"))
        row.analytes_note = get("analytes_note")
        row.last_sampled = parse_date(get("last_sampled"))
        row.source_url = get("source_url")
        row.note = get("note")
    session.commit()
    return {"added": added, "updated": updated, "skipped": skipped, "columns": mapping}
