"""Finding public business details for the companies and people in the records.

A use report names an applicator business and a licence number and nothing
else.  To turn that into a useful company profile the tracker looks up the
business's public contact details — website, office phone, business address,
licence status.

Order matters, because the sources are not equally good:

1. **The records themselves.**  County permits already carry the business
   name, telephone number, licence number and expiry date, parsed during
   import.  This is the best source available and costs nothing.
2. **Authoritative registries.**  DPR's licensing database for pesticide
   licences, the Secretary of State for business registration.  These produce
   ``verified`` facts.
3. **Web search**, last.  It finds a company's own website and published
   contact page, which is legitimate and useful, but it is also where wrong
   and personal information comes from.  Web-sourced facts are therefore
   candidates: they are classified by :mod:`.policy`, and nothing reaches a
   public page until an administrator approves it.

Search is off unless a provider and key are configured.  The tracker does not
crawl the open web by default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.config import Settings, get_settings
from app.core.confidence import Confidence
from app.core.provenance import ExtractionMethod, Provenance, SourceType
from app.providers.enrichment.policy import Disposition, FactKind, PolicyDecision, classify


class EnrichmentError(RuntimeError):
    pass


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""


class SearchBackend(Protocol):
    name: str

    def search(self, query: str, *, limit: int = 5) -> list[SearchResult]: ...


class DisabledSearch:
    """The default. Explains itself rather than silently returning nothing."""

    name = "disabled"

    def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        raise EnrichmentError(
            "web enrichment is not configured; set TRACKER_WEB_SEARCH_PROVIDER and "
            "TRACKER_WEB_SEARCH_API_KEY to enable it"
        )


class HttpSearchBackend:
    """A search API returning JSON.

    Written against the shape the common search APIs share (Brave, Google
    Programmable Search, Bing): a query parameter, a key header, and a list of
    results with a title, a URL and a snippet. The field paths are configurable
    so switching provider is configuration rather than code.
    """

    def __init__(
        self,
        *,
        name: str,
        endpoint: str,
        api_key: str,
        key_header: str = "X-Subscription-Token",
        query_param: str = "q",
        results_path: tuple[str, ...] = ("web", "results"),
        client: httpx.Client | None = None,
    ) -> None:
        self.name = name
        self._endpoint = endpoint
        self._headers = {key_header: api_key, "Accept": "application/json"}
        self._query_param = query_param
        self._results_path = results_path
        self._client = client or httpx.Client(timeout=30.0)

    def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        try:
            response = self._client.get(
                self._endpoint,
                params={self._query_param: query, "count": limit},
                headers=self._headers,
            )
            response.raise_for_status()
            payload: Any = response.json()
        except httpx.HTTPError as exc:
            raise EnrichmentError(f"search request failed: {exc}") from exc
        except ValueError as exc:
            raise EnrichmentError("the search provider returned an unreadable response") from exc

        for key in self._results_path:
            if not isinstance(payload, dict):
                return []
            payload = payload.get(key, [])
        if not isinstance(payload, list):
            return []

        results = []
        for item in payload[:limit]:
            url = item.get("url") or item.get("link") or ""
            if not url:
                continue
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=str(url),
                    snippet=str(item.get("description") or item.get("snippet") or ""),
                )
            )
        return results


def get_search_backend(settings: Settings | None = None) -> SearchBackend:
    settings = settings or get_settings()
    if settings.web_search_provider in ("disabled", "", None):
        return DisabledSearch()
    if not settings.web_search_api_key or not settings.web_search_endpoint:
        return DisabledSearch()
    return HttpSearchBackend(
        name=settings.web_search_provider,
        endpoint=settings.web_search_endpoint,
        api_key=settings.web_search_api_key,
    )


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

@dataclass
class EnrichedFact:
    """One candidate detail about a company or person."""

    kind: FactKind
    value: str
    decision: PolicyDecision
    provenance: Provenance

    @property
    def publishable(self) -> bool:
        return self.decision.disposition == Disposition.PUBLIC

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "value": self.value,
            "disposition": self.decision.disposition.value,
            "policy_reason": self.decision.reason,
            "source": self.provenance.describe(),
            "source_url": self.provenance.source_url,
            "confidence": self.provenance.confidence,
        }


@dataclass
class EnrichmentResult:
    subject: str
    facts: list[EnrichedFact] = field(default_factory=list)
    searched: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def publishable(self) -> list[EnrichedFact]:
        return [f for f in self.facts if f.publishable]

    @property
    def needs_review(self) -> list[EnrichedFact]:
        return [f for f in self.facts if f.decision.disposition == Disposition.REVIEW]

    @property
    def withheld(self) -> list[EnrichedFact]:
        return [f for f in self.facts if f.decision.disposition == Disposition.RESTRICTED]

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "facts": [f.to_dict() for f in self.facts],
            "publishable_count": len(self.publishable),
            "review_count": len(self.needs_review),
            "withheld_count": len(self.withheld),
            "queries": self.searched,
            "errors": self.errors,
        }


_PHONE = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

#: Result hosts that are directories rather than the business itself. Their
#: listings are frequently stale or wrong, so they are not used as a source.
_DIRECTORY_HOSTS = (
    "yelp.", "bbb.org", "manta.com", "buzzfile.", "dnb.com", "zoominfo.",
    "bizapedia.", "opencorporates.", "facebook.com", "linkedin.com",
    "whitepages.", "spokeo.", "truepeoplesearch.", "fastpeoplesearch.",
)


def _is_official_site(url: str, company: str) -> bool:
    """Whether a result looks like the company's own website."""
    lowered = url.lower()
    if any(host in lowered for host in _DIRECTORY_HOSTS):
        return False
    from app.core.normalize import normalize_company

    tokens = [t.lower() for t in normalize_company(company).core_tokens if len(t) > 3]
    host = re.sub(r"^https?://(www\.)?", "", lowered).split("/")[0]
    return any(token in host for token in tokens)


class BusinessEnricher:
    """Finds public contact details for a business named in the records."""

    def __init__(
        self,
        *,
        search: SearchBackend | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.search = search or get_search_backend(self.settings)

    def enrich_company(
        self,
        name: str,
        *,
        license_number: str | None = None,
        county: str | None = None,
    ) -> EnrichmentResult:
        """Look up a company's public business details.

        The licence number is included in the query when known: it is the most
        distinguishing term available and keeps the search from drifting onto a
        similarly-named business in another state.
        """
        result = EnrichmentResult(subject=name)

        terms = [f'"{name}"']
        if license_number:
            terms.append(license_number)
        terms.append("pest control business OR applicator OR forestry")
        if county:
            terms.append(f"{county} County California")
        query = " ".join(terms)
        result.searched.append(query)

        try:
            hits = self.search.search(query, limit=6)
        except EnrichmentError as exc:
            result.errors.append(str(exc))
            return result

        for hit in hits:
            official = _is_official_site(hit.url, name)
            if not official:
                # Directory listings are skipped entirely: a wrong address on a
                # public profile about a named business is a real harm.
                continue

            provenance = Provenance(
                source_type=SourceType.DERIVED,
                source_name=hit.title or hit.url,
                source_url=hit.url,
                extraction_method=ExtractionMethod.API,
                # Web-sourced facts are never better than medium: the page was
                # found by a search engine, not verified by a registry.
                confidence=Confidence.MEDIUM,
                notes=(f"found by {self.search.name} search",),
            )

            self._add(result, FactKind.WEBSITE, hit.url, provenance, is_business=True)

            text = f"{hit.title} {hit.snippet}"
            phone = _PHONE.search(text)
            if phone:
                self._add(
                    result, FactKind.BUSINESS_PHONE, phone.group(0), provenance, is_business=True
                )
            for email in set(_EMAIL.findall(text)):
                self._add(result, FactKind.BUSINESS_EMAIL, email, provenance, is_business=True)

        return result

    @staticmethod
    def _add(
        result: EnrichmentResult,
        kind: FactKind,
        value: str,
        provenance: Provenance,
        *,
        is_business: bool,
    ) -> None:
        value = value.strip()
        if not value or any(f.kind == kind and f.value == value for f in result.facts):
            return
        decision = classify(kind, value, subject_is_business=is_business)
        result.facts.append(EnrichedFact(kind, value, decision, provenance))


def facts_from_permit_contact(
    *,
    name: str,
    phone: str | None,
    license_number: str | None,
    contact_type: str | None,
    permit_number: str | None,
    source_name: str,
) -> EnrichmentResult:
    """Business details taken from a county permit's contact list.

    The best enrichment source the tracker has, because it is an official
    record rather than a search result: these facts are ``verified``.
    """
    result = EnrichmentResult(subject=name)
    provenance = Provenance(
        source_type=SourceType.RESTRICTED_MATERIALS_PERMIT,
        source_name=source_name,
        source_id=permit_number,
        extraction_method=ExtractionMethod.DOCX_TABLE,
        confidence=Confidence.VERIFIED,
        locator="contact list",
    )
    if phone:
        BusinessEnricher._add(result, FactKind.BUSINESS_PHONE, phone, provenance, is_business=True)
    if license_number:
        BusinessEnricher._add(
            result, FactKind.LICENSE_NUMBER, license_number, provenance, is_business=True
        )
    if contact_type:
        BusinessEnricher._add(
            result, FactKind.JOB_TITLE, contact_type, provenance, is_business=True
        )
    return result
