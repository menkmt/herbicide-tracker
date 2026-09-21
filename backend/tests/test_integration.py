"""End-to-end: files in, published applications out.

Requires PostGIS. Set ``TRACKER_TEST_DATABASE_URL`` to run.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def session(database_url, tmp_path):
    from app.models import Base

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker() as value:
        yield value
    engine.dispose()


@pytest.fixture
def storage(tmp_path):
    from app.pipeline.storage import LocalSourceStorage

    return LocalSourceStorage(tmp_path / "storage")


def test_a_batch_of_county_documents_becomes_reviewable_applications(
    session, storage, fixtures_dir
):
    from app.models import ApplicationCluster, FactSource, Permit, PurRecord
    from app.pipeline.ingest import ingest_files

    summary = ingest_files(
        session,
        [
            fixtures_dir / "use_records.tsv",
            fixtures_dir / "permit.docx_text.txt",
            fixtures_dir / "use_report_form.txt",
        ],
        county="Lassen",
        storage=storage,
    )
    session.commit()

    assert summary.files_processed == 3
    assert summary.records_extracted == 3
    assert summary.permits_extracted == 1
    assert summary.clusters_created >= 2

    # Source records are preserved exactly as reported.
    assert session.scalar(select(PurRecord).where(PurRecord.document_number == "WEB2115761161"))
    assert session.scalar(select(Permit).where(Permit.permit_number == "18-24-4500033"))

    # Every published fact can be traced to its document.
    assert session.scalar(select(FactSource).limit(1)) is not None

    # The form's landowner became an application title.
    titles = {c.title for c in session.scalars(select(ApplicationCluster)).all()}
    assert "Terry Barr" in titles


def test_reimporting_the_same_files_creates_nothing(session, storage, fixtures_dir):
    from app.pipeline.ingest import ingest_files

    paths = [fixtures_dir / "use_records.tsv"]
    ingest_files(session, paths, county="Lassen", storage=storage)
    session.commit()
    again = ingest_files(session, paths, county="Lassen", storage=storage)
    session.commit()

    assert again.files_duplicate == 1
    assert again.records_extracted == 0
    assert again.clusters_created == 0


def test_restricted_chemicals_are_flagged_from_the_county_permit(
    session, storage, fixtures_dir
):
    from app.models import ActiveIngredient, ChemicalFlagRow
    from app.pipeline.ingest import ingest_files

    ingest_files(
        session,
        [fixtures_dir / "permit.docx_text.txt", fixtures_dir / "use_records.tsv"],
        county="Lassen",
        storage=storage,
    )
    session.commit()

    flags = session.scalars(select(ChemicalFlagRow)).all()
    by_subject = {(f.subject_name, f.reason) for f in flags}
    assert ("2,4-D", "california_restricted_material") in by_subject
    assert ("2,4-D", "publisher_watchlist") in by_subject
    assert ("Hexazinone", "publisher_watchlist") in by_subject
    # The watchlist entry must never be recorded as a legal restriction.
    hexazinone = session.scalar(
        select(ActiveIngredient).where(ActiveIngredient.slug == "hexazinone")
    )
    assert hexazinone.is_watchlisted
    assert not hexazinone.is_california_restricted


def test_only_published_applications_are_publicly_visible(session, storage, fixtures_dir):
    from app.models import ApplicationCluster
    from app.pipeline.ingest import ingest_files

    ingest_files(session, [fixtures_dir / "use_records.tsv"], county="Lassen", storage=storage)
    session.commit()

    statuses = {c.status for c in session.scalars(select(ApplicationCluster)).all()}
    assert "published" not in statuses
