"""Who is behind an application, and photos of the people named on permits.

The "parties" block on an application page comes from the permits the use
reports were filed under: the operator (permittee), the agent who applied
for the permit, the qualified applicator licences and the pest control
businesses listed on it, and the property owner. Each line says which
document it came from.

Photos are uploaded by an administrator, who records where each came from.
They are re-encoded on upload, which strips embedded location and camera
metadata, and capped in size.
"""

from __future__ import annotations

import hashlib
import io
import re
import tempfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import rate_limit, require_admin
from app.core.access import Principal
from app.core.normalize import company_key, normalize_person
from app.db import get_session
from app.extraction.permit import person_name
from app.models import Company, Permit, PermitContact, Person
from app.pipeline.storage import get_storage

router = APIRouter(prefix="/api", tags=["people"])

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_EDGE_PX = 640

#: Contact-list types that mean a qualified applicator licence or certificate.
QAL_TYPES = re.compile(r"\bQA[LC]\b", re.IGNORECASE)
#: Contact-list types that mean a licensed pest control business.
PCB_TYPES = re.compile(r"\bPC[BM]\b|pest control business", re.IGNORECASE)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _person_by_name(session: Session, name: str | None) -> Person | None:
    if not name:
        return None
    key = normalize_person(name).key
    return session.scalar(select(Person).where(Person.name_key == key)) if key else None


def _photo_path(person: Person) -> str | None:
    # Site-relative: the public site proxies it, the browser never calls the API.
    return f"/api/people/{person.slug}/photo" if person.photo_url else None


def _company(session: Session, name: str | None) -> dict | None:
    if not name:
        return None
    row = session.scalar(select(Company).where(Company.name_key == company_key(name)))
    if row is None:
        return {"name": name}
    # Web-found details publish only when the site showed the permit's phone
    # number, or an administrator approved them.
    confirmed = row.contact_review_state in ("auto_verified", "approved")
    return {
        "name": row.name,
        "slug": row.slug,
        "website": row.website if confirmed else None,
        "email": row.email if confirmed else None,
        "phone": row.phone,
        "phone_source": row.phone_source,
        "contact_evidence": row.contact_evidence if confirmed else None,
        "address": row.business_address,
        "license": row.dpr_license,
    }


def matching_permits(session: Session, numbers: list[str], on_date: date | None) -> list[Permit]:
    """The permits a use report's permit reference points to.

    Use reports usually cite the operator number ("4500033") rather than the
    full permit number ("18-24-4500033"), and an operator holds a new permit
    every few years under the same number. So match on either, and prefer the
    permit that was in force on the application's date.
    """
    if not numbers:
        return []
    conditions = []
    for n in numbers:
        conditions += [Permit.permit_number == n, Permit.permit_number.like(f"%-{n}"),
                       Permit.operator_id == n]
    found = session.scalars(
        select(Permit).where(or_(*conditions)).order_by(Permit.expires_on.desc().nullslast())
    ).all()
    if on_date:
        in_force = [
            p for p in found
            if (p.valid_from or p.issued_on or date.min) <= on_date <= (p.expires_on or date.max)
        ]
        if in_force:
            return in_force
    return list(found)


def parties_for(
    session: Session,
    permit_numbers: list[str],
    owner_name: str | None,
    on_date: date | None = None,
) -> dict:
    """Owner, operator, agent, qualified applicators and contractors."""
    permits = matching_permits(session, permit_numbers, on_date)

    owner = _company(session, owner_name)
    operator = None
    agents: dict[str, dict] = {}
    qals: dict[str, dict] = {}
    businesses: dict[str, dict] = {}

    for permit in permits:
        contacts = session.scalars(
            select(PermitContact).where(PermitContact.permit_id == permit.id)
        ).all()
        if operator is None and permit.operator_name:
            operator = _company(session, permit.operator_name) or {"name": permit.operator_name}
            primary = next(
                (c for c in contacts if c.license_number and c.phone
                 and (c.contact_type or "").upper() in ("AR", "GROWER-PERMITTEE")),
                None,
            )
            if primary and not operator.get("phone"):
                operator["phone"] = primary.phone
                operator["phone_source"] = f"permit {permit.permit_number} contact list"
            operator["permit_number"] = permit.permit_number
            operator["operator_id"] = primary.license_number if primary else None

        for raw, role, title in (
            (permit.agent_name, "Agent on the permit", None),
            (permit.applicant_name, "Signed the permit application", permit.applicant_title),
        ):
            name = person_name(raw)
            if not name:
                continue
            key = normalize_person(name).key
            entry = agents.setdefault(key, {"name": name, "roles": [], "permits": [],
                                            "title": None, "photo": None, "photo_source": None})
            if role not in entry["roles"]:
                entry["roles"].append(role)
            if permit.permit_number not in entry["permits"]:
                entry["permits"].append(permit.permit_number)
            entry["title"] = entry["title"] or title
            person = _person_by_name(session, name)
            if person and person.photo_url:
                entry["photo"] = _photo_path(person)
                entry["photo_source"] = person.photo_source

        for c in contacts:
            ctype = c.contact_type or ""
            if QAL_TYPES.search(ctype) and c.license_number:
                qals.setdefault(c.license_number, {
                    "license": c.license_number,
                    "held_under": c.name,
                    "type": ctype,
                    "expires": c.license_expiration.isoformat() if c.license_expiration else None,
                    "permit": permit.permit_number,
                })
            elif PCB_TYPES.search(ctype) and c.license_number:
                found = _company(session, c.name) or {"name": c.name}
                businesses.setdefault(c.license_number, {
                    **found,
                    "license": c.license_number,
                    "phone": c.phone or found.get("phone"),
                    "expires": c.license_expiration.isoformat() if c.license_expiration else None,
                    "permit": permit.permit_number,
                })

    return {
        "owner": owner,
        "operator": operator,
        "people": list(agents.values()),
        "qualified_applicators": list(qals.values()),
        "contractors": list(businesses.values()),
        "source_note": (
            "From the restricted materials permit(s) the use reports were filed under. "
            "Website and email appear once they have been found and checked."
        ),
    }


@router.get("/people/{slug}/photo")
def person_photo(slug: str, session: Session = Depends(get_session),
                 _: Principal = Depends(rate_limit)) -> Response:
    person = session.scalar(select(Person).where(Person.slug == slug))
    if person is None or not person.photo_url or not person.photo_url.startswith("storage:"):
        raise HTTPException(404, "No photo")
    data = get_storage().open(person.photo_url.removeprefix("storage:"))
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.post("/admin/people/photo")
async def upload_person_photo(
    name: str = Form(..., min_length=3, max_length=160),
    source: str = Form(..., min_length=3, max_length=500),
    employer: str | None = Form(None),
    title: str | None = Form(None),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    _: Principal = Depends(require_admin),
) -> dict:
    """Attach a photo to a person named on permits.

    ``source`` is required: where the photo came from (a URL, "taken by …",
    "supplied by …"). It is shown under the photo.
    """
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "The photo is larger than 8 MB")
    try:
        image = Image.open(io.BytesIO(raw))
        image = image.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(400, "That file is not an image") from exc
    image.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX))
    out = io.BytesIO()
    # Re-encoding drops EXIF, including any GPS position the camera recorded.
    image.save(out, format="JPEG", quality=85, optimize=True)
    data = out.getvalue()

    parsed = normalize_person(name)
    if not parsed.key:
        raise HTTPException(400, "Give the person's full name")
    person = session.scalar(select(Person).where(Person.name_key == parsed.key))
    if person is None:
        person = Person(display_name=parsed.display or name.strip(), name_key=parsed.key,
                        slug=_slug(parsed.display or name), given_name=parsed.given or None,
                        family_name=parsed.family or None)
        session.add(person)
    if employer:
        company = session.scalar(select(Company).where(Company.name_key == company_key(employer)))
        if company is not None:
            person.employer_id = company.id
    if title:
        person.job_title = title

    digest = hashlib.sha256(data).hexdigest()
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / f"{person.slug or digest[:12]}.jpg"
        path.write_bytes(data)
        key = get_storage().put(path, sha256=digest, filename=path.name)
    person.photo_url = f"storage:{key}"
    person.photo_source = source.strip()
    person.is_published = True
    session.commit()
    return {"person": person.display_name, "slug": person.slug, "photo": _photo_path(person),
            "bytes": len(data)}
