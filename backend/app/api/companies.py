"""Admin: review the website, phone and email found for each company."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.config import get_settings
from app.core.access import Principal
from app.db import get_session
from app.models import Company
from app.providers.enrichment.budget import usage

router = APIRouter(prefix="/api/admin/companies", tags=["admin"])


def _row(c: Company) -> dict:
    return {
        "id": c.id, "name": c.name, "type": c.business_type, "license": c.dpr_license,
        "website": c.website, "phone": c.phone, "phone_source": c.phone_source,
        "email": c.email, "state": c.contact_review_state, "evidence": c.contact_evidence,
        "checked_at": c.contact_checked_at.isoformat() if c.contact_checked_at else None,
    }


@router.get("")
def list_companies(session: Session = Depends(get_session),
                   _: Principal = Depends(require_admin)) -> dict:
    order = {"pending": 0, "none": 1, "auto_verified": 2, "approved": 3, "rejected": 4}
    rows = sorted(session.scalars(select(Company)).all(),
                  key=lambda c: (order.get(c.contact_review_state, 9), c.name))
    settings = get_settings()
    return {
        "companies": [_row(c) for c in rows],
        "search": {
            "enabled": settings.web_search_provider not in ("disabled", "", None)
            and bool(settings.web_search_api_key),
            "used_this_month": usage(session),
            "monthly_limit": settings.web_search_monthly_limit,
        },
    }


class ContactDecision(BaseModel):
    decision: str  # approve | reject | edit
    website: str | None = None
    email: str | None = None
    phone: str | None = None


@router.post("/{company_id}/contact")
def decide(company_id: int, body: ContactDecision, session: Session = Depends(get_session),
           _: Principal = Depends(require_admin)) -> dict:
    c = session.get(Company, company_id)
    if c is None:
        raise HTTPException(404, "No such company")
    if body.decision == "reject":
        c.website = c.email = None
        c.contact_review_state = "rejected"
    elif body.decision in ("approve", "edit"):
        for field in ("website", "email", "phone"):
            value = getattr(body, field)
            if value is not None:
                setattr(c, field, value.strip() or None)
                if field == "phone" and value.strip():
                    c.phone_source = "entered by an administrator"
        c.contact_review_state = "approved"
        if body.decision == "edit":
            c.contact_evidence = "entered by an administrator"
    else:
        raise HTTPException(400, "decision must be approve, reject or edit")
    session.commit()
    return _row(c)


@router.post("/lookup")
def run_lookup(limit: int = 15, session: Session = Depends(get_session),
               _: Principal = Depends(require_admin)) -> dict:
    """Run the website lookup now for companies not checked recently."""
    from app.jobs.enrich_companies import enrich

    report = enrich(session, limit=max(1, min(limit, 40)))
    return {"text": report.render(), "checked": report.checked,
            "verified": report.verified, "pending": report.pending,
            "not_found": report.not_found}
