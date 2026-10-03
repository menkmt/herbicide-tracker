"""Find website, phone and email for every business the records name.

Businesses come from the permits (the operator, the pest control businesses
on the contact list), the use reports (applicator businesses) and the
applications (property owners). Each gets a company record carrying the
phone its permit lists; then its website is found and read (see
app.providers.enrichment.website).

    python -m app.jobs.enrich_companies             # up to 40 not checked lately
    python -m app.jobs.enrich_companies --all       # everything, ignoring recency
    python -m app.jobs.enrich_companies --name "PP Forestry LLC"

Runs on every deploy, a few dozen companies at a time, and rechecks a
company after 90 days.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.normalize import company_key
from app.models import ApplicationCluster, Company, Permit, PermitContact, PurRecord
from app.providers.enrichment.business import DisabledSearch, get_search_backend
from app.providers.enrichment.website import WebsiteFinder

RECHECK_AFTER = timedelta(days=90)
BUSINESS_CONTACT = re.compile(r"\bPC[BM]\b|pest control|grower|permittee|^AR$", re.IGNORECASE)


@dataclass
class EnrichReport:
    companies_created: int = 0
    checked: int = 0
    verified: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    not_found: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"companies created: {self.companies_created}", f"checked: {self.checked}"]
        if self.verified:
            lines.append("verified (published): " + "; ".join(self.verified))
        if self.pending:
            lines.append("found, waiting for your approval: " + "; ".join(self.pending))
        if self.not_found:
            lines.append("no website found: " + ", ".join(self.not_found))
        return "\n".join(lines)


def _slug(session: Session, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:200] or "company"
    slug, n = base, 2
    while session.scalar(select(Company.id).where(Company.slug == slug)):
        slug, n = f"{base}-{n}", n + 1
    return slug


def ensure_company(session: Session, name: str | None, *, phone: str | None = None,
                   phone_source: str | None = None, license_number: str | None = None,
                   business_type: str | None = None, report: EnrichReport | None = None
                   ) -> Company | None:
    if not name or not name.strip():
        return None
    key = company_key(name)
    if not key:
        return None
    row = session.scalar(select(Company).where(Company.name_key == key))
    if row is None:
        row = Company(name=name.strip().rstrip("."), name_key=key, slug=_slug(session, name))
        session.add(row)
        session.flush()
        if report:
            report.companies_created += 1
    if phone and not row.phone:
        row.phone, row.phone_source = phone, phone_source
    if license_number and not row.dpr_license:
        row.dpr_license = license_number
    if business_type and not row.business_type:
        row.business_type = business_type
    return row


def collect_companies(session: Session, report: EnrichReport) -> None:
    """Make sure every business named in the records has a company row."""
    # Newest permits first, so a company's displayed phone is its current one.
    for permit in session.scalars(
        select(Permit).order_by(Permit.expires_on.desc().nullslast())
    ).all():
        contacts = session.scalars(
            select(PermitContact).where(PermitContact.permit_id == permit.id)
        ).all()
        primary = next((c for c in contacts if (c.contact_type or "").upper()
                        in ("AR", "GROWER-PERMITTEE") and c.phone), None)
        source = f"permit {permit.permit_number} contact list"
        ensure_company(session, permit.operator_name,
                       phone=primary.phone if primary else None,
                       phone_source=source if primary else None,
                       business_type="operator", report=report)
        for c in contacts:
            if c.is_business and BUSINESS_CONTACT.search(c.contact_type or ""):
                ensure_company(session, c.name, phone=c.phone,
                               phone_source=f"permit {permit.permit_number} contact list",
                               license_number=c.license_number if re.search(
                                   r"PC[BM]|pest control", c.contact_type or "", re.I) else None,
                               business_type="pest_control_business" if re.search(
                                   r"PC[BM]|pest control", c.contact_type or "", re.I)
                               else "operator", report=report)
    for (owner,) in session.execute(
        select(ApplicationCluster.owner_name).where(ApplicationCluster.owner_name.is_not(None))
        .distinct()
    ):
        ensure_company(session, owner, business_type="landowner", report=report)
    for name, lic in session.execute(
        select(PurRecord.applicator_name, PurRecord.applicator_license)
        .where(PurRecord.applicator_name.is_not(None)).distinct()
    ):
        ensure_company(session, name, license_number=lic, business_type="applicator",
                       report=report)
    session.commit()


def known_phones(session: Session, company: Company) -> list[str]:
    """Every phone a county permit has listed for this business."""
    phones = [company.phone] if company.phone else []
    for c in session.scalars(select(PermitContact).where(PermitContact.phone.is_not(None))):
        if c.name and company_key(c.name) == company.name_key and c.phone not in phones:
            phones.append(c.phone)
    return phones


def enrich(session: Session, *, limit: int = 40, everything: bool = False,
           name: str | None = None, finder: WebsiteFinder | None = None) -> EnrichReport:
    report = EnrichReport()
    collect_companies(session, report)

    search = get_search_backend()
    finder = finder or WebsiteFinder(search=None if isinstance(search, DisabledSearch) else search)

    stmt = select(Company).where(Company.contact_review_state.notin_(("approved", "rejected")))
    if name:
        stmt = select(Company).where(Company.name_key == company_key(name))
    elif not everything:
        stale = datetime.now(UTC) - RECHECK_AFTER
        stmt = stmt.where(or_(Company.contact_checked_at.is_(None),
                              Company.contact_checked_at < stale))
    for company in session.scalars(stmt.order_by(Company.id).limit(limit)).all():
        finding = finder.find(company.name, known_phone=known_phones(session, company),
                              license_number=company.dpr_license)
        company.contact_checked_at = datetime.now(UTC)
        report.checked += 1
        if finding.status == "verified":
            company.website = finding.website
            company.email = finding.email or company.email
            if not company.phone:
                company.phone, company.phone_source = finding.phone, "company website"
            company.contact_source_url = finding.website
            company.contact_evidence = finding.evidence
            company.contact_review_state = "auto_verified"
            report.verified.append(f"{company.name} -> {finding.website}")
        elif finding.status == "likely":
            company.website = finding.website
            company.email = finding.email
            company.contact_source_url = finding.website
            company.contact_evidence = finding.evidence + (
                f"; phone(s) on the site: {', '.join(finding.phones)}" if finding.phones else "")
            company.contact_review_state = "pending"
            report.pending.append(f"{company.name} -> {finding.website}")
        else:
            report.not_found.append(company.name)
        session.commit()
    return report


def main() -> int:
    from app.db import SessionLocal

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--name")
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()
    session = SessionLocal()
    try:
        print(enrich(session, limit=args.limit, everything=args.all, name=args.name).render())
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
