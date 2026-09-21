"""Command-line entry point for the monthly CPRA refresh.

Run from cron, a systemd timer or a container scheduler:

    python -m app.jobs.run_cpra_sync

It reads the watermark from the last successful run, asks Inquisitor for
anything county agricultural commissioners have produced since, and feeds the
new documents through the ordinary import pipeline — so a CPRA-delivered file
goes through exactly the same extraction, clustering and review as one an
administrator drops in by hand.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.jobs.inquisitor_sync import CpraSyncConfig, SyncedFile, run_monthly_sync
from app.models import CpraSyncRun, SourceFile
from app.pipeline.ingest import ingest_files
from app.providers.inquisitor import InquisitorClient, InquisitorError

logger = logging.getLogger("cpra_sync")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pull new CPRA productions from Inquisitor")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be imported without storing anything",
    )
    parser.add_argument(
        "--no-campaign",
        action="store_true",
        help="skip opening a new CPRA campaign; only collect existing productions",
    )
    parser.add_argument("--county", default=None, help="county to attribute imported files to")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = get_settings()

    if not settings.inquisitor_enabled:
        logger.error(
            "the CPRA sync is disabled; set TRACKER_INQUISITOR_ENABLED=true and the "
            "connection settings to enable it"
        )
        return 2

    config = CpraSyncConfig(
        enabled=True,
        base_url=settings.inquisitor_base_url,
        workspace_id=settings.inquisitor_workspace_id,
        actor=settings.inquisitor_actor,
        open_campaign=settings.inquisitor_open_campaign and not args.no_campaign,
        auto_send=settings.inquisitor_auto_send,
        coverage_start=settings.coverage_start,
    )

    with session_scope() as session:
        last = session.scalar(
            select(CpraSyncRun)
            .where(CpraSyncRun.succeeded.is_(True))
            .order_by(CpraSyncRun.started_at.desc())
            .limit(1)
        )
        watermark = last.watermark if last else None

        def have_sha256(digest: str) -> bool:
            return (
                session.scalar(select(SourceFile.id).where(SourceFile.sha256 == digest))
                is not None
            )

        collected: list[SyncedFile] = []

        try:
            client = InquisitorClient(
                config.base_url,
                workspace_id=config.workspace_id,
                actor=config.actor,
            )
        except InquisitorError as exc:
            logger.error("%s", exc)
            return 2

        with client:
            result = run_monthly_sync(
                client,
                config,
                last_watermark=watermark,
                have_sha256=have_sha256,
                ingest=collected.append,
            )

        logger.info("%s", result.summary())
        for note in result.notes:
            logger.info("  %s", note)
        for error in result.errors:
            logger.warning("  %s", error)

        if args.dry_run:
            logger.info("dry run: %d file(s) would be imported", len(collected))
            return 0

        # Write the downloaded bytes to a temporary directory and hand them to
        # the same importer an administrator's upload uses.
        summary = None
        if collected:
            with tempfile.TemporaryDirectory() as tmpdir:
                paths: list[Path] = []
                metadata: dict[str, dict] = {}
                for synced in collected:
                    path = Path(tmpdir) / Path(synced.vault_file.filename).name
                    path.write_bytes(synced.content)
                    paths.append(path)
                    metadata[path.name] = {
                        "agency": synced.vault_file.agency,
                        "request_number": synced.vault_file.request_number,
                        "production": synced.vault_file.production,
                        "source_id": synced.vault_file.id,
                    }
                summary = ingest_files(
                    session,
                    paths,
                    county=args.county,
                    label=f"CPRA sync {datetime.now(UTC):%Y-%m-%d}",
                    origin="cpra",
                    cpra_metadata=metadata,
                )
                logger.info("\n%s", summary.render())

        session.add(
            CpraSyncRun(
                started_at=result.started_at,
                finished_at=datetime.now(UTC),
                window_start=result.window_start,
                window_end=result.window_end,
                watermark=result.new_watermark or watermark,
                campaign_id=result.campaign.id if result.campaign else None,
                files_seen=result.files_seen,
                files_ingested=result.files_ingested,
                succeeded=result.succeeded,
                detail={
                    "sync": result.to_dict(),
                    "import": summary.to_dict() if summary else None,
                },
            )
        )

    return 0 if result.succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
