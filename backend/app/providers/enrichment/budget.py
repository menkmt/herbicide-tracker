"""A monthly cap on paid web searches.

Every search is counted in the database before it is made, with a row lock,
so the cap holds across restarts, deploys and concurrent workers. Once the
month's count reaches the limit, searches are refused (the website finder
then falls back to guessing the address, which costs nothing).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SearchUsage
from app.providers.enrichment.business import EnrichmentError, SearchBackend, SearchResult


def this_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def usage(session: Session) -> int:
    row = session.get(SearchUsage, this_month())
    return row.count if row else 0


class BudgetedSearch:
    def __init__(self, inner: SearchBackend, session: Session, monthly_limit: int) -> None:
        self.inner = inner
        self.session = session
        self.monthly_limit = max(0, monthly_limit)
        self.name = f"{inner.name} (capped at {self.monthly_limit}/month)"
        self.refused = 0

    def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        month = this_month()
        row = self.session.scalar(
            select(SearchUsage).where(SearchUsage.month == month).with_for_update()
        )
        if row is None:
            row = SearchUsage(month=month, count=0)
            self.session.add(row)
            self.session.flush()
        if row.count >= self.monthly_limit:
            self.session.commit()
            self.refused += 1
            raise EnrichmentError(
                f"monthly search limit of {self.monthly_limit} reached; searching resumes "
                f"next month"
            )
        row.count += 1
        # Committed before the request, so a crash mid-search still counts it.
        self.session.commit()
        return self.inner.search(query, limit=limit)
