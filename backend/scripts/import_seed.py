"""Load the bundled sample records, place them on the map, and publish them.

Runs on every deploy (deploy/autodeploy.sh). It is idempotent: a file already
imported is recognised by its hash and skipped, so after the first run this
only fetches outlines for any sections still missing one.

    python -m scripts.import_seed            # import, locate, publish ready
    python -m scripts.import_seed --no-publish
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.maps.sections import ensure_section_geometry
from app.models import ApplicationCluster, SourceFile
from app.pipeline.ingest import ingest_files, sha256_file

SEED_ROOT = Path(__file__).resolve().parent.parent / "seed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-publish", action="store_true")
    args = parser.parse_args()

    session = SessionLocal()
    try:
        for county_dir in sorted(p for p in SEED_ROOT.glob("*") if p.is_dir()):
            county = county_dir.name.replace("-", " ").title()
            files = [
                p for p in sorted(county_dir.iterdir())
                if p.is_file() and not p.name.startswith(".")
                and session.scalar(
                    select(SourceFile.id).where(SourceFile.sha256 == sha256_file(p))
                ) is None
            ]
            if not files:
                print(f"[{county}] sample records already loaded")
                continue
            summary = ingest_files(
                session, files, county=county, label=f"{county} sample records", origin="seed"
            )
            session.commit()
            print(f"[{county}] {summary.files_processed} file(s) imported, "
                  f"{summary.records_extracted} records, {summary.clusters_created} applications")

        report = ensure_section_geometry(session)
        print(report.render())

        if not args.no_publish:
            ready = session.scalars(
                select(ApplicationCluster).where(ApplicationCluster.status == "ready")
            ).all()
            for cluster in ready:
                cluster.status = "published"
                cluster.published_at = datetime.now(UTC)
            session.commit()
            print(f"published {len(ready)} application(s) the pipeline marked ready")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
