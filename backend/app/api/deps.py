"""Request-scoped dependencies: who is calling, and what they may do."""

from __future__ import annotations

import hashlib
import logging
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.access import ANONYMOUS, AccessDenied, Capability, Principal, Tier
from app.db import get_session
from app.models import ApiKey

logger = logging.getLogger(__name__)


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
    """Sliding-window rate limiter, shared across workers when Redis is up.

    Rate limiting is part of the commercial offering — the public tier gets a
    modest budget and subscribers get a larger one — so the counters have to be
    shared. A per-process limiter silently multiplies every limit by the worker
    count, which is the kind of bug that is invisible until someone notices
    their paid quota was never really enforced.

    When Redis is unreachable the limiter keeps working per-process rather than
    failing requests: refusing traffic because a cache is down is worse than
    counting it imprecisely for a few minutes. It says so in the logs once,
    rather than on every request.
    """

    def __init__(self, redis_url: str | None = None) -> None:
        self._local: dict[str, deque[float]] = defaultdict(deque)
        self._redis: Any | None = None
        self._warned = False
        if redis_url:
            try:
                import redis

                self._redis = redis.Redis.from_url(
                    redis_url, socket_timeout=0.25, socket_connect_timeout=0.25
                )
            except Exception:  # noqa: BLE001 - never let this break startup
                self._redis = None

    def check(self, key: str, limit: int, window: float = 60.0) -> tuple[bool, int]:
        """Record a hit and report whether it is within budget."""
        if self._redis is not None:
            try:
                return self._check_redis(key, limit, window)
            except Exception as exc:  # noqa: BLE001 - Redis is not load-bearing
                if not self._warned:
                    logger.warning(
                        "rate limiting fell back to per-process counting (%s); with "
                        "several workers the effective limit is now the configured "
                        "limit times the worker count",
                        exc,
                    )
                    self._warned = True
        return self._check_local(key, limit, window)

    def _check_redis(self, key: str, limit: int, window: float) -> tuple[bool, int]:
        """Count hits in a Redis sorted set keyed by timestamp.

        A sorted set rather than a counter so the window slides: an INCR with
        an expiry resets on a fixed boundary, which lets a caller send two
        full budgets back to back across the boundary.
        """
        now = time.time()
        redis_key = f"ratelimit:{key}"
        pipeline = self._redis.pipeline()
        pipeline.zremrangebyscore(redis_key, 0, now - window)
        pipeline.zadd(redis_key, {f"{now}:{id(pipeline)}": now})
        pipeline.zcard(redis_key)
        pipeline.expire(redis_key, int(window) + 1)
        count = pipeline.execute()[2]
        if count > limit:
            # Remove the hit we just added so a rejected request does not
            # extend the caller's lockout.
            self._redis.zremrangebyscore(redis_key, now, now)
            return False, 0
        return True, limit - count

    def _check_local(self, key: str, limit: int, window: float) -> tuple[bool, int]:
        now = time.monotonic()
        hits = self._local[key]
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) >= limit:
            return False, 0
        hits.append(now)
        return True, limit - len(hits)


limiter = RateLimiter(get_settings().redis_url)


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
