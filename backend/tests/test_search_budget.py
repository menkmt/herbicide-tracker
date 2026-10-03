import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.providers.enrichment.budget import BudgetedSearch, usage
from app.providers.enrichment.business import EnrichmentError, SearchResult


class Counting:
    name = "fake"

    def __init__(self):
        self.calls = 0

    def search(self, query, *, limit=5):
        self.calls += 1
        return [SearchResult(title="x", url="https://example.com")]


@pytest.fixture
def db_session(database_url):
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS search_usage "
                          "(month varchar(7) PRIMARY KEY, count integer NOT NULL)"))
        conn.execute(text("DELETE FROM search_usage"))
    with Session(engine) as session:
        yield session
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM search_usage"))


def test_searches_stop_at_the_monthly_limit(db_session):
    inner = Counting()
    capped = BudgetedSearch(inner, db_session, monthly_limit=2)
    capped.search("a")
    capped.search("b")
    with pytest.raises(EnrichmentError, match="limit of 2 reached"):
        capped.search("c")
    assert inner.calls == 2
    assert usage(db_session) == 2
