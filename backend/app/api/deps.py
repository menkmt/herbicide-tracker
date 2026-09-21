"""Request-scoped dependencies: who is calling, and what they may do."""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.access import ANONYMOUS, AccessDenied, Capability, Principal, Tier
from app.db import get_session
from app.models import ApiKey


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def get_principal(
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> Principal:
    """Identify the caller from an API key, falling back to anonymous."""
    raw = x_api_key
    if not raw and authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    if not raw:
        return ANONYMOUS

    if settings.admin_token and raw == settings.admin_token:
        return Principal(tier=Tier.ADMIN, subject="admin-token")

    row = session.scalar(
        select(ApiKey).where(ApiKey.key_hash == hash_key(raw), ApiKey.revoked_at.is_(None))
    )
    if row is None:
        # An invalid key is rejected rather than silently downgraded, so a
        # subscriber notices a typo instead of quietly hitting public limits.
        raise HTTPException(status_code=401, detail="Unknown or revoked API key")

    request.state.api_key_id = row.id
    return Principal(
        tier=Tier(row.tier),
        key_id=str(row.id),
        subject=row.subject,
        watermark_seed=row.watermark_seed,
    )


def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access is required")
    return principal


def require_capability(capability: Capability):
    """Dependency factory guarding one capability."""

    def guard(principal: Principal = Depends(get_principal)) -> Principal:
        try:
            from app.core.access import require

            require(principal, capability)
        except AccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return principal

    return guard


class RateLimiter:
    """Small in-process sliding-window limiter.

    Deliberately simple and honest about its scope: it protects a single
    process.  A multi-worker deployment should front this with the Redis
    limiter (see ``docs/DEPLOYMENT.md``); this exists so a default install is
    not wide open.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window: float = 60.0) -> tuple[bool, int]:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) >= limit:
            return False, 0
        hits.append(now)
        return True, limit - len(hits)


limiter = RateLimiter()


def rate_limit(
    request: Request,
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_settings),
) -> Principal:
    limit = (
        settings.subscriber_rate_per_minute
        if principal.tier != Tier.PUBLIC
        else settings.anonymous_rate_per_minute
    )
    identity = principal.key_id or (request.client.host if request.client else "unknown")
    allowed, remaining = limiter.check(f"{principal.tier}:{identity}", limit)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": "60"},
        )
    request.state.rate_remaining = remaining
    return principal
