"""What contact information the tracker may publish.

The tracker publishes who applied herbicides, for whom, and under which
licence.  That is public-record information about commercial activity and it
belongs on the site.  It is also the point at which a public-interest tracker
can most easily go wrong, so the rule is drawn explicitly here rather than
being left to whoever writes the next enrichment provider.

The distinction is **business versus personal**, not *findable versus
unfindable*.  A company's office address, switchboard number, website and DPR
licence are how that business presents itself to the public, and they publish.
A named individual's home address, personal mobile number, personal email
address or a photograph lifted from their social media are personal, and they
do not — even when they are easy to find, and even when the individual is the
operator.

Property owners are the case that needs most care.  An owner's name and the
parcel's own situs address come from the assessor's public roll and describe
the property, so they publish.  An owner's *mailing* address does not: for an
individual it is usually their home, and the tracker has no need of it.

Everything classified ``restricted`` is still stored — it can matter for
matching two records to the same person — but it is never rendered on a public
page and never returned by the public API.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Disposition(StrEnum):
    #: Publishable on the public site.
    PUBLIC = "public"
    #: Stored for matching and admin use; never rendered publicly.
    RESTRICTED = "restricted"
    #: Needs a person to decide before it is published.
    REVIEW = "review"


class FactKind(StrEnum):
    BUSINESS_NAME = "business_name"
    BUSINESS_ADDRESS = "business_address"
    BUSINESS_PHONE = "business_phone"
    BUSINESS_EMAIL = "business_email"
    WEBSITE = "website"
    LICENSE_NUMBER = "license_number"
    JOB_TITLE = "job_title"
    EMPLOYER = "employer"
    PROFESSIONAL_PHOTO = "professional_photo"

    PARCEL_OWNER_NAME = "parcel_owner_name"
    PARCEL_SITUS_ADDRESS = "parcel_situs_address"
    OWNER_MAILING_ADDRESS = "owner_mailing_address"

    PERSONAL_ADDRESS = "personal_address"
    PERSONAL_PHONE = "personal_phone"
    PERSONAL_EMAIL = "personal_email"
    SOCIAL_MEDIA_PHOTO = "social_media_photo"
    DATE_OF_BIRTH = "date_of_birth"


#: The default disposition of each kind of fact.
DISPOSITIONS: dict[FactKind, Disposition] = {
    FactKind.BUSINESS_NAME: Disposition.PUBLIC,
    FactKind.BUSINESS_ADDRESS: Disposition.PUBLIC,
    FactKind.BUSINESS_PHONE: Disposition.PUBLIC,
    FactKind.BUSINESS_EMAIL: Disposition.PUBLIC,
    FactKind.WEBSITE: Disposition.PUBLIC,
    FactKind.LICENSE_NUMBER: Disposition.PUBLIC,
    FactKind.JOB_TITLE: Disposition.PUBLIC,
    FactKind.EMPLOYER: Disposition.PUBLIC,
    FactKind.PROFESSIONAL_PHOTO: Disposition.REVIEW,
    FactKind.PARCEL_OWNER_NAME: Disposition.PUBLIC,
    FactKind.PARCEL_SITUS_ADDRESS: Disposition.PUBLIC,
    FactKind.OWNER_MAILING_ADDRESS: Disposition.REVIEW,
    FactKind.PERSONAL_ADDRESS: Disposition.RESTRICTED,
    FactKind.PERSONAL_PHONE: Disposition.RESTRICTED,
    FactKind.PERSONAL_EMAIL: Disposition.RESTRICTED,
    FactKind.SOCIAL_MEDIA_PHOTO: Disposition.RESTRICTED,
    FactKind.DATE_OF_BIRTH: Disposition.RESTRICTED,
}

#: Email addresses at these providers are personal accounts, whoever uses them
#: for work.
_CONSUMER_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "me.com", "comcast.net", "msn.com", "live.com", "proton.me",
}

#: Hosts whose images are personal even when publicly visible.
_SOCIAL_HOSTS = (
    "facebook.com", "fbcdn.net", "instagram.com", "cdninstagram.com",
    "twitter.com", "x.com", "twimg.com", "tiktok.com", "pinterest.",
)


@dataclass(frozen=True)
class PolicyDecision:
    disposition: Disposition
    reason: str

    @property
    def is_public(self) -> bool:
        return self.disposition == Disposition.PUBLIC


def classify_email(address: str, *, subject_is_business: bool) -> PolicyDecision:
    """Decide whether an email address may be published."""
    domain = address.split("@")[-1].strip().lower() if "@" in address else ""
    if domain in _CONSUMER_EMAIL_DOMAINS:
        return PolicyDecision(
            Disposition.RESTRICTED,
            f"{domain} is a consumer email provider, so this is a personal address",
        )
    if not domain:
        return PolicyDecision(Disposition.RESTRICTED, "not a well-formed email address")
    if re.match(r"^(info|office|contact|sales|admin|support|hello|enquiries)@", address.lower()):
        return PolicyDecision(Disposition.PUBLIC, "a business's general contact address")
    if subject_is_business:
        return PolicyDecision(Disposition.PUBLIC, "an address on the business's own domain")
    return PolicyDecision(
        Disposition.REVIEW,
        "an individual's address on a company domain; publishable, but confirm it is "
        "their work address first",
    )


def classify_photo(url: str) -> PolicyDecision:
    lowered = url.lower()
    if any(host in lowered for host in _SOCIAL_HOSTS):
        return PolicyDecision(
            Disposition.RESTRICTED,
            "images from social media are personal and are never published",
        )
    return PolicyDecision(
        Disposition.REVIEW,
        "confirm this is a professional photograph published by the person's employer "
        "or a professional body before using it",
    )


def classify(kind: FactKind, value: str, *, subject_is_business: bool = True) -> PolicyDecision:
    """Decide the disposition of one enriched fact."""
    if kind == FactKind.BUSINESS_EMAIL:
        return classify_email(value, subject_is_business=subject_is_business)
    if kind in (FactKind.PROFESSIONAL_PHOTO, FactKind.SOCIAL_MEDIA_PHOTO):
        return classify_photo(value)
    if kind == FactKind.OWNER_MAILING_ADDRESS and not subject_is_business:
        return PolicyDecision(
            Disposition.RESTRICTED,
            "an individual landowner's mailing address is usually their home address",
        )

    disposition = DISPOSITIONS.get(kind, Disposition.REVIEW)
    reasons = {
        Disposition.PUBLIC: "business or public-record information about commercial activity",
        Disposition.RESTRICTED: "personal information, stored for matching but never published",
        Disposition.REVIEW: "needs a person to confirm before publication",
    }
    return PolicyDecision(disposition, reasons[disposition])
