"""Find a company's own website and read its public contact details.

Two ways in, cheapest first:

1. **Guess the domain.** Small forestry and pest-control businesses almost
   always use their name as their web address: "WM Beaty & Associates" ->
   wmbeaty.com, "Western Helicopter Services" -> westernhelicopter.com. A
   handful of candidates are tried; no search service is needed.
2. **Search**, when a search API key is configured (see business.py).

A candidate site is then read — the home page and up to four contact or
about pages — for telephone numbers, email addresses and the company's name.

Whether a site is *the* company is the part that matters, because a wrong
phone number on a public profile is a real harm. The rule:

* **verified** — the site shows the telephone number the county permit lists
  for this business. That is an official record agreeing with the site.
* **likely** — the site's name or title carries the company's distinctive
  name words but no permit phone was available to compare. Held for review.
* anything less is discarded.

Only verified findings publish without a person looking at them.
"""

from __future__ import annotations

import re
import urllib.robotparser
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import urljoin, urlsplit

import httpx

from app.providers.enrichment.business import _DIRECTORY_HOSTS, SearchBackend
from app.providers.enrichment.policy import Disposition, classify_email

USER_AGENT = "HerbicideTrackerCA/1.0 (public-records research; +https://herbicidetracker.com/about)"
CONTACT_HINTS = ("contact", "about", "office", "location", "team", "staff")
TLDS = (".com", ".net", ".org", ".us")
#: Words too generic to identify a business on their own.
GENERIC = {
    "INC", "LLC", "CO", "CORP", "COMPANY", "THE", "AND", "OF", "SERVICES", "SERVICE",
    "ASSOC", "ASSOCIATES", "ASSOCIATION", "GROUP", "ENTERPRISES", "INDUSTRIES",
    "CALIFORNIA", "CA", "PEST", "CONTROL", "FORESTRY", "FOREST", "TIMBER", "LAND",
    "MANAGEMENT", "PRODUCTS", "RESOURCES", "LUMBER",
}

_PHONE = re.compile(r"(?<!\d)\(?(\d{3})\)?[\s.\-]?(\d{3})[\s.\-]?(\d{4})(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_HREF = re.compile(r"""href\s*=\s*["']([^"'#]+)["']""", re.IGNORECASE)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.IGNORECASE | re.DOTALL)


def digits(phone: str | None) -> str:
    d = re.sub(r"\D", "", phone or "")
    return d[-10:] if len(d) >= 10 else ""


def pretty_phone(d: str) -> str:
    return f"({d[:3]}) {d[3:6]}-{d[6:]}"


LEGAL = {"inc", "llc", "co", "corp", "corporation", "ltd", "lp", "llp", "the", "and", "of"}


def raw_words(name: str) -> list[str]:
    """The name's own words, lower-cased, without punctuation or legal suffixes."""
    words = re.findall(r"[a-z0-9]+", (name or "").lower().replace("&", " and "))
    return [w for w in words if w not in LEGAL]


def distinctive_tokens(name: str) -> list[str]:
    """Words that identify this business rather than its line of work."""
    return [w for w in raw_words(name) if w.upper() not in GENERIC and len(w) >= 2]


def candidate_domains(name: str) -> list[str]:
    """Plausible web addresses for a business name, most likely first.

    Never a single generic word (western.com, sierra.com): those are someone
    else's site, and trying them only wastes requests.
    """
    words = raw_words(name)
    if not words:
        return []
    distinct = distinctive_tokens(name)
    trimmed = list(words)
    while len(trimmed) > 1 and trimmed[-1].upper() in GENERIC:
        trimmed.pop()                          # drop "services", "company"…
    stems: list[str] = []
    for parts in (words, trimmed, distinct, words[:2]):
        stem = "".join(parts)
        if len(parts) >= 2 or (parts == words and len(words) == 1):
            if len(stem) >= 5 and stem not in stems:
                stems.append(stem)
    initials = "".join(w[0] for w in words)
    if len(words) >= 3 and initials not in stems:
        stems.append(initials + "inc")
    return [f"{stem}{tld}" for stem in stems[:5] for tld in TLDS[:2]]


@dataclass
class SiteFinding:
    company: str
    website: str | None = None
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    status: str = "not_found"          # verified | likely | not_found
    evidence: str = ""
    pages_read: list[str] = field(default_factory=list)
    tried: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def phone(self) -> str | None:
        return self.phones[0] if self.phones else None

    @property
    def email(self) -> str | None:
        return self.emails[0] if self.emails else None


class WebsiteFinder:
    def __init__(self, *, search: SearchBackend | None = None,
                 client: httpx.Client | None = None, max_pages: int = 5) -> None:
        self.search = search
        self.client = client or httpx.Client(
            timeout=12.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
        self.max_pages = max_pages
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    # --- discovery ----------------------------------------------------------

    def candidates(self, name: str, license_number: str | None, county: str | None) -> list[str]:
        urls = [f"https://{d}" for d in candidate_domains(name)]
        if self.search is not None:
            query = f'"{name}"' + (f" {county} County" if county else "") + " California"
            try:
                for hit in self.search.search(query, limit=6):
                    host = urlsplit(hit.url).hostname or ""
                    if host and not any(d in host for d in _DIRECTORY_HOSTS):
                        urls.insert(0, f"https://{host}")
            except Exception:  # noqa: BLE001 - search is a bonus, never a blocker
                pass
        seen: list[str] = []
        for u in urls:
            if u not in seen:
                seen.append(u)
        return seen

    def find(self, name: str, *, known_phone: str | list[str] | None = None,
             license_number: str | None = None, county: str | None = None) -> SiteFinding:
        """``known_phone`` may be several numbers: a business's permits over
        the years often list different offices, and any of them counts."""
        finding = SiteFinding(company=name)
        known = [known_phone] if isinstance(known_phone, str) else list(known_phone or [])
        targets = [d for d in (digits(p) for p in known) if d]
        target = targets[0] if targets else ""
        tokens = distinctive_tokens(name)
        best: SiteFinding | None = None

        for base in self.candidates(name, license_number, county):
            finding.tried.append(base)
            pages = self._read_site(base, finding)
            if not pages:
                continue
            site_url, texts, html_titles = pages
            text_all = " ".join(texts)
            phones = []
            for m in _PHONE.finditer(text_all):
                d = "".join(m.groups())
                if d not in phones and not d.startswith(("000", "555", "123")):
                    phones.append(d)
            host = (urlsplit(site_url).hostname or "").removeprefix("www.")
            emails = []
            for e in _EMAIL.findall(text_all):
                e = e.strip(".").lower()
                if e.endswith((".png", ".jpg", ".gif", ".webp", ".svg")) or "example." in e:
                    continue
                if classify_email(e, subject_is_business=True).disposition != Disposition.PUBLIC:
                    continue
                if e not in emails:
                    emails.append(e)
            # Prefer addresses on the company's own domain.
            emails.sort(key=lambda e: 0 if e.endswith("@" + host) or e.endswith("." + host) else 1)

            # The company's distinctive words must be in the site's own name —
            # its address or page title — not merely somewhere in the text.
            ident = (host + " " + " ".join(html_titles)).lower()
            name_hit = bool(tokens) and all(t in ident for t in tokens if len(t) >= 3) \
                and any(len(t) >= 3 for t in tokens)
            candidate = SiteFinding(company=name, website=site_url, emails=emails[:3],
                                    pages_read=finding.pages_read, tried=finding.tried,
                                    errors=finding.errors)
            matched = next((t for t in targets if t in phones), None)
            if matched:
                target = matched
                candidate.status = "verified"
                candidate.evidence = (
                    f"the site shows {pretty_phone(target)}, the number on the county permit"
                )
                candidate.phones = [pretty_phone(target)] + [
                    pretty_phone(p) for p in phones if p != target][:1]
                return candidate
            if name_hit:
                candidate.status = "likely"
                candidate.phones = [pretty_phone(p) for p in phones[:2]]
                candidate.evidence = (
                    "the site's name and pages carry the company's name"
                    + (f", but not the permit's phone {pretty_phone(target)}" if target else
                       "; no permit phone was available to confirm it")
                )
                best = best or candidate
        return best or finding

    # --- fetching -------------------------------------------------------------

    def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.client.get(root + "/robots.txt")
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
                self._robots[root] = rp
            except httpx.HTTPError:
                self._robots[root] = None
        rp = self._robots[root]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    def _get(self, url: str, finding: SiteFinding) -> tuple[str, str] | None:
        if not self._allowed(url):
            return None
        try:
            r = self.client.get(url)
        except httpx.HTTPError as exc:
            finding.errors.append(f"{url}: {type(exc).__name__}")
            return None
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200 or "html" not in ctype:
            return None
        finding.pages_read.append(str(r.url))
        return str(r.url), r.text[:400_000]

    def _read_site(self, base: str, finding: SiteFinding):
        home = self._get(base, finding)
        if home is None and base.startswith("https://"):
            home = self._get("http://" + base.removeprefix("https://"), finding)
        if home is None:
            return None
        site_url, html = home
        host = urlsplit(site_url).hostname
        titles = [unescape(t).strip() for t in _TITLE.findall(html)]
        texts = [self._text(html)]
        links = []
        for href in _HREF.findall(html):
            if href.lower().startswith("tel:"):
                texts.append(href[4:])
                continue
            if href.lower().startswith("mailto:"):
                texts.append(href[7:].split("?")[0])
                continue
            full = urljoin(site_url, href)
            if urlsplit(full).hostname == host and any(h in full.lower() for h in CONTACT_HINTS):
                if full not in links:
                    links.append(full)
        for link in links[: self.max_pages - 1]:
            page = self._get(link, finding)
            if page:
                _, phtml = page
                texts.append(self._text(phtml))
                for href in _HREF.findall(phtml):
                    if href.lower().startswith(("tel:", "mailto:")):
                        texts.append(href.split(":", 1)[1].split("?")[0])
        return site_url, texts, titles

    @staticmethod
    def _text(html: str) -> str:
        return re.sub(r"\s+", " ", unescape(_TAGS.sub(" ", html)))
