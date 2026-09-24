"""First-party traffic measurement.

Two endpoints: the public site posts one record per page view, and the
administrator reads totals. Built in rather than pulled from Google
Analytics so that nothing about who reads a public record leaves this
server, and so the dashboard works without a third account.

What is kept: the path, the day, the referring site's host, and a daily
salted hash standing in for the visitor. What is not: IP addresses, user
agents, cookies, or anything that links one day's visits to the next.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import rate_limit, require_admin
from app.config import Settings, get_settings
from app.core.access import Principal
from app.db import get_session
from app.models import PageView

router = APIRouter(prefix="/api", tags=["analytics"])

# Crawlers and monitors are not readers. The list is deliberately broad: an
# undercounted human is a smaller error than a counted bot.
BOT_PATTERN = re.compile(
    r"bot|crawl|spider|slurp|fetch|scan|monitor|preview|headless|lighthouse|"
    r"curl|wget|python-requests|go-http-client|java/|facebookexternalhit|"
    r"whatsapp|telegram|discord|skype|embedly|quora|pinterest|semrush|ahrefs",
    re.IGNORECASE,
)


def is_bot(user_agent: str | None) -> bool:
    if not user_agent or len(user_agent) < 12:
        return True
    return bool(BOT_PATTERN.search(user_agent))


def visitor_hash(ip: str, user_agent: str, day: date, secret: str) -> str:
    """Daily-rotating pseudonym for a visitor.

    The salt includes the day, so the same person is one visitor within a
    day and unrelatable across days; and the secret, so nobody outside the
    server can recompute the hash from a known address.
    """
    material = f"{secret}|{day.isoformat()}|{ip}|{user_agent}"
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def referrer_host(referrer: str | None, own_host: str | None) -> str | None:
    if not referrer:
        return None
    host = urlsplit(referrer).hostname
    if not host:
        return None
    host = host.lower().removeprefix("www.")
    if own_host and host == own_host.lower().removeprefix("www."):
        return None
    return host[:160]


def clean_path(path: str) -> str:
    """The path alone: no query string, so an address someone searched for
    on /near-me is never written down."""
    path = path.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    return path[:512]


class TrackEvent(BaseModel):
    path: str = Field(max_length=2048)
    referrer: str | None = Field(default=None, max_length=2048)


@router.post("/track", status_code=204)
def track(
    event: TrackEvent,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _: Principal = Depends(rate_limit),
    user_agent: str | None = Header(default=None),
    # Set by the site's own server, which sits between the browser and this
    # API and is the only thing that can reach it.
    client_ip: str | None = Header(default=None, alias="X-Tracker-Client-IP"),
    client_ua: str | None = Header(default=None, alias="X-Tracker-Client-UA"),
    country: str | None = Header(default=None, alias="X-Tracker-Country"),
) -> None:
    ua = client_ua or user_agent
    if is_bot(ua):
        return None
    path = clean_path(event.path)
    if path.startswith("/api/") or path.startswith("/_next/"):
        return None
    now = datetime.now(UTC)
    ip = client_ip or (request.client.host if request.client else "0.0.0.0")
    own_host = urlsplit(settings.public_base_url).hostname
    session.add(
        PageView(
            ts=now,
            day=now.date(),
            path=path,
            referrer_host=referrer_host(event.referrer, own_host),
            visitor=visitor_hash(ip, ua or "", now.date(), settings.secret_key),
            country=(country or "")[:2].upper() or None,
        )
    )
    session.commit()
    return None


def _window(session: Session, since: date) -> dict:
    views = session.scalar(select(func.count()).where(PageView.day >= since)) or 0
    visitors = (
        session.scalar(
            select(func.count(func.distinct(PageView.visitor))).where(PageView.day >= since)
        )
        or 0
    )
    return {"views": int(views), "visitors": int(visitors)}


@router.get("/admin/analytics")
def analytics(
    days: int = 30,
    session: Session = Depends(get_session),
    _: Principal = Depends(require_admin),
) -> dict:
    """Traffic summary for the admin dashboard."""
    days = max(1, min(days, 365))
    today = datetime.now(UTC).date()
    since = today - timedelta(days=days - 1)

    per_day_rows = session.execute(
        select(PageView.day, func.count(), func.count(func.distinct(PageView.visitor)))
        .where(PageView.day >= since)
        .group_by(PageView.day)
    ).all()
    by_day = {d: (v, u) for d, v, u in per_day_rows}
    per_day = [
        {
            "day": (since + timedelta(days=i)).isoformat(),
            "views": int(by_day.get(since + timedelta(days=i), (0, 0))[0]),
            "visitors": int(by_day.get(since + timedelta(days=i), (0, 0))[1]),
        }
        for i in range(days)
    ]

    top_pages = session.execute(
        select(PageView.path, func.count(), func.count(func.distinct(PageView.visitor)))
        .where(PageView.day >= since)
        .group_by(PageView.path)
        .order_by(func.count().desc())
        .limit(15)
    ).all()
    top_referrers = session.execute(
        select(PageView.referrer_host, func.count())
        .where(PageView.day >= since, PageView.referrer_host.is_not(None))
        .group_by(PageView.referrer_host)
        .order_by(func.count().desc())
        .limit(10)
    ).all()
    countries = session.execute(
        select(PageView.country, func.count(func.distinct(PageView.visitor)))
        .where(PageView.day >= since, PageView.country.is_not(None))
        .group_by(PageView.country)
        .order_by(func.count(func.distinct(PageView.visitor)).desc())
        .limit(10)
    ).all()

    return {
        "today": _window(session, today),
        "last_7_days": _window(session, today - timedelta(days=6)),
        "window": {"days": days, **_window(session, since)},
        "per_day": per_day,
        "top_pages": [
            {"path": p, "views": int(v), "visitors": int(u)} for p, v, u in top_pages
        ],
        "top_referrers": [{"host": h, "views": int(v)} for h, v in top_referrers],
        "countries": [{"country": c, "visitors": int(v)} for c, v in countries],
        "note": (
            "Visitors are counted once per day by a salted hash that rotates daily; "
            "no addresses or cookies are stored. Known crawlers are excluded."
        ),
    }
