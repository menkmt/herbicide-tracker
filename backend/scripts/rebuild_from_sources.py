"""Re-import every stored document through the current rules.

For when the importer has improved — a new duplicate check, a new way of
telling notices of intent from use reports — and what is already in the
database was read under the old rules. Every original document the server
holds is read again, oldest first, exactly as if it had just been uploaded.

    python -m scripts.rebuild_from_sources            # dry run: says what it would do
    python -m scripts.rebuild_from_sources --yes      # do it

Kept untouched: the original files, people and their photos, companies and
their contact details, county commissioners and records status, inspection
logs, water stations, visitor counts, THP / project maps (and which
applications they were linked to). Rebuilt: use records, notices,
permits, applications and the review queue. Applications the pipeline marks
ready are published again; anything held for review waits for review again.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from geoalchemy2.shape import to_shape
from sqlalchemy import select, text

from app.api.projects import locate_in_project
from app.db import SessionLocal
from app.maps.sections import ensure_section_geometry
from app.models import ApplicationCluster, County, Project, SourceFile
from app.pipeline.ingest import ingest_files
from app.pipeline.storage import get_storage

#: Tables cleared before the re-import, children first.
CLEAR = (
    "application_parcels", "application_parties", "maps", "application_cluster_records",
    "application_clusters", "pur_products", "permit_contacts", "permit_materials",
    "permit_sites", "review_items", "import_batches",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="actually rebuild")
    args = parser.parse_args()

    session = SessionLocal()
    storage = get_storage()
    sources = session.scalars(
        select(SourceFile)
        .where(SourceFile.document_kind.is_distinct_from("inspection_log"))
        .order_by(SourceFile.created_at, SourceFile.id)
    ).all()
    readable = [s for s in sources if s.storage_key and storage.exists(s.storage_key)]
    missing = [s.filename for s in sources if s not in readable]
    print(f"{len(readable)} stored document(s) will be re-read, oldest first")
    if missing:
        print(f"{len(missing)} cannot be re-read because the original is not held here "
              f"(they will be left out): {', '.join(missing[:10])}")
    if not args.yes:
        print("dry run - nothing changed. Run again with --yes to rebuild.")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="tracker-rebuild-"))
    plan = []
    for s in readable:
        dest = tmp / f"{s.id:06d}"
        dest.mkdir()
        target = dest / Path(s.filename.split(" › ")[-1]).name
        shutil.copyfile(storage.local_copy(s.storage_key), target)
        county = session.get(County, s.county_id).name if s.county_id else None
        plan.append((target, county, s.origin or "upload"))

    # Project links live on the applications being rebuilt; remember them by
    # slug and put them back afterwards.
    project_links = dict(session.execute(
        select(ApplicationCluster.slug, ApplicationCluster.project_id)
        .where(ApplicationCluster.project_id.is_not(None))).all())

    # Clear derived data. Records and permits go after their children; source
    # file rows are recreated by the re-import (inspection logs are kept).
    session.execute(text("UPDATE pur_records SET fulfilled_by_record_id = NULL"))
    for table in CLEAR:
        session.execute(text(f"DELETE FROM {table}"))
    session.execute(text("DELETE FROM pur_records"))
    session.execute(text("DELETE FROM permits"))
    session.execute(text(
        "DELETE FROM fact_sources WHERE source_file_id IN "
        "(SELECT id FROM source_files WHERE document_kind IS DISTINCT FROM 'inspection_log')"))
    session.execute(text(
        "DELETE FROM source_files WHERE document_kind IS DISTINCT FROM 'inspection_log'"))
    session.commit()

    totals = {"records": 0, "duplicates": 0, "applications": 0, "failed": 0}
    for path, county, origin in plan:
        summary = ingest_files(session, [path], county=county, label="rebuild", origin=origin)
        session.commit()
        totals["records"] += summary.records_extracted - summary.records_duplicate
        totals["duplicates"] += summary.records_duplicate
        totals["applications"] += summary.clusters_created
        totals["failed"] += summary.files_failed
        print(f"  {path.name}: {summary.records_extracted} read, "
              f"{summary.records_duplicate} already on file, "
              f"{summary.clusters_created} application(s)"
              + (f", FAILED: {summary.files[0].error}" if summary.files_failed else ""))
    shutil.rmtree(tmp, ignore_errors=True)

    print(ensure_section_geometry(session).render())
    relinked = 0
    for slug, project_id in project_links.items():
        cluster = session.scalar(select(ApplicationCluster).where(ApplicationCluster.slug == slug))
        project = session.get(Project, project_id)
        if cluster is None or project is None:
            continue
        cluster.project_id = project.id
        if project.geom is not None:
            locate_in_project(session, cluster, to_shape(project.geom))
        relinked += 1
    if project_links:
        print(f"{relinked} of {len(project_links)} project map link(s) restored")
    ready = session.scalars(
        select(ApplicationCluster).where(ApplicationCluster.status == "ready")).all()
    for c in ready:
        c.status, c.published_at = "published", datetime.now(UTC)
    session.commit()
    print(f"\nrebuilt: {totals['records']} records kept, {totals['duplicates']} duplicates "
          f"dropped, {totals['applications']} applications, {len(ready)} published, "
          f"{totals['failed']} file(s) could not be read")
    return 0


if __name__ == "__main__":
    sys.exit(main())
