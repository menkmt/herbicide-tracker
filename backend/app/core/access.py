"""Access tiers, and an honest account of what "not scrapable" can mean.

The tracker is intended to become a paid product, so the data needs
protecting.  It is worth being precise about what is achievable, because the
build plan also requires SEO-indexable public pages, and those two goals pull
in opposite directions: anything a search engine can crawl, anyone can copy.
There is no configuration that makes public HTML unreadable to a determined
copier.

What *is* achievable, and what this module implements, is making the
**valuable** form of the data — bulk, structured, current, and complete — a
paid product, while the public pages stay a shop window:

* **Public tier** — SEO-indexable application, chemical and profile pages, and
  a modest page size.  Enough to be useful, findable and citable.
* **Subscriber tier** — the API, large pages, bulk export, full aggregate
  analytics and the cross-county rollups.  Requires a key.
* **Admin tier** — import, review and publication.

Alongside the tiers, the measures that actually raise the cost of wholesale
copying are: rate limiting per key and per address, no unauthenticated bulk
or export endpoint, aggregate figures gated behind a key, and canary records
that make a copied dataset identifiable.  None of these is a guarantee, and
the module does not pretend otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Tier(StrEnum):
    PUBLIC = "public"
    SUBSCRIBER = "subscriber"
    ADMIN = "admin"


class Capability(StrEnum):
    """Individual things a caller may be allowed to do."""

    #: Read a single published application, chemical or profile page.
    READ_PUBLIC = "read_public"
    #: Search and filter published applications.
    SEARCH = "search"
    #: Address/radius search.
    RADIUS_SEARCH = "radius_search"
    #: Aggregate totals across counties, companies and chemicals.
    READ_AGGREGATES = "read_aggregates"
    #: Page sizes beyond the public limit.
    LARGE_PAGES = "large_pages"
    #: Bulk download of records in a machine-readable format.
    BULK_EXPORT = "bulk_export"
    #: Unpublished records, review queue and provenance internals.
    READ_UNPUBLISHED = "read_unpublished"
    #: Import, review, publish.
    ADMINISTER = "administer"


TIER_CAPABILITIES: dict[Tier, frozenset[Capability]] = {
    Tier.PUBLIC: frozenset(
        {Capability.READ_PUBLIC, Capability.SEARCH, Capability.RADIUS_SEARCH}
    ),
    Tier.SUBSCRIBER: frozenset(
        {
            Capability.READ_PUBLIC,
            Capability.SEARCH,
            Capability.RADIUS_SEARCH,
            Capability.READ_AGGREGATES,
            Capability.LARGE_PAGES,
            Capability.BULK_EXPORT,
        }
    ),
    Tier.ADMIN: frozenset(Capability),
}


@dataclass(frozen=True)
class Principal:
    """Who is making a request."""

    tier: Tier = Tier.PUBLIC
    key_id: str | None = None
    subject: str | None = None
    #: Identifies which subscriber a leaked export came from.
    watermark_seed: str | None = None

    def can(self, capability: Capability) -> bool:
        return capability in TIER_CAPABILITIES[self.tier]

    @property
    def is_admin(self) -> bool:
        return self.tier == Tier.ADMIN


ANONYMOUS = Principal()


class AccessDenied(PermissionError):
    """Raised when a caller lacks a capability.

    Carries a human-readable upgrade path, because for a commercial product
    "403" is a sales opportunity rather than only an error.
    """

    def __init__(self, capability: Capability, principal: Principal) -> None:
        self.capability = capability
        self.principal = principal
        if principal.tier == Tier.PUBLIC:
            hint = (
                "This endpoint is part of the subscriber tier. "
                "Contact Protect Lassen for an API key."
            )
        else:
            hint = "Your key does not include this capability."
        super().__init__(f"{capability.value} is not available on the {principal.tier} tier. {hint}")


def require(principal: Principal, capability: Capability) -> None:
    if not principal.can(capability):
        raise AccessDenied(capability, principal)


def max_page_size(principal: Principal, *, public: int, subscriber: int) -> int:
    return subscriber if principal.can(Capability.LARGE_PAGES) else public


def clamp_page_size(principal: Principal, requested: int | None, *, public: int, subscriber: int) -> int:
    limit = max_page_size(principal, public=public, subscriber=subscriber)
    if requested is None:
        return min(25, limit)
    return max(1, min(requested, limit))
