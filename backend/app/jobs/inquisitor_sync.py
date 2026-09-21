"""Monthly CPRA refresh: pull newly produced records from Inquisitor.

This is what keeps the tracker current without anyone re-uploading anything.
Once a month the job:

1. asks Inquisitor to open a fresh CPRA campaign covering the period since the
   last successful sync, targeting California county agricultural
   commissioners;
2. lists evidence-vault files produced since the last watermark;
3. skips anything the tracker already holds, by SHA-256;
4. downloads the remaining originals and hands them to the ordinary import
   pipeline, carrying the CPRA chain of custody through as provenance.

Two deliberate restraints:

* **Sending is opt-in.**  The campaign is *created* automatically, but its
  targets are only dispatched when ``auto_send`` is set.  A records request
  sent to a public agency is correspondence from the publisher, and the
  default is that a person approves it going out.
* **The watermark only advances on success.**  A partial sync leaves the
  watermark where it was, so the next run re-examines the same window rather
  than silently skipping a production that failed to download.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.core.coverage import COVERAGE_START
from app.core.provenance import ExtractionMethod, Provenance, SourceType
from app.providers.inquisitor import (
    Campaign,
    InquisitorClient,
    InquisitorError,
    VaultFile,
)

logger = logging.getLogger(__name__)

#: Default CPRA command issued to Inquisitor's campaign planner.  It is a
#: natural-language instruction because that is Inquisitor's interface; the
#: planner turns it into a per-agency target matrix.
DEFAULT_CAMPAIGN_COMMAND = (
    "Send California Public Records Act requests to every California county "
    "agricultural commissioner for the following records covering {since} "
    "through {until}: (1) all pesticide use reports, including the site ID, "
    "township, range, section, meridian, application date and time, product "
    "name, EPA registration number, quantity applied, treated acreage, "
    "application method, operator and applicator for each report; (2) all "
    "notices of intent filed for restricted material applications; and (3) all "
    "restricted materials permits, including the complete contact list, "
    "pesticides list and site list with MTRS and permitted acreage for each "
    "permit. Request machine-readable formats (CSV, Excel, or a native "
    "database export) in preference to scanned documents where the county can "
    "provide them."
)

#: Overlap applied to the watermark so a production filed late, or backdated by
#: the agency, is not missed between runs.
WATERMARK_OVERLAP = timedelta(days=14)


@dataclass
class CpraSyncConfig:
    """Everything the monthly sync needs to know."""

    enabled: bool = False
    base_url: str = ""
    workspace_id: str = ""
    actor: str = ""
    #: Create a new CPRA campaign on each run.
    open_campaign: bool = True
    #: Dispatch the campaign's targets automatically.  Off by default.
    auto_send: bool = False
    #: Restrict the sync to particular agencies (Inquisitor agency IDs).
    agency_ids: tuple[str, ...] = ()
    command_template: str = DEFAULT_CAMPAIGN_COMMAND
    #: The tracker covers 2020 onward, so a first run asks for everything from
    #: the coverage start rather than a rolling window.
    coverage_start: date = COVERAGE_START

    def build_command(self, since: date, until: date) -> str:
        return self.command_template.format(since=since.isoformat(), until=until.isoformat())


@dataclass
class SyncedFile:
    """A vault file the tracker decided to ingest."""

    vault_file: VaultFile
    content: bytes
    provenance: Provenance


@dataclass
class SyncResult:
    """What one monthly run did, for the admin dashboard and the audit log."""

    started_at: datetime
    window_start: date
    window_end: date
    campaign: Campaign | None = None
    targets_sent: int = 0
    files_seen: int = 0
    files_skipped_not_importable: int = 0
    files_skipped_duplicate: int = 0
    files_ingested: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    new_watermark: date | None = None

    @property
    def succeeded(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        """One-line summary for the import dashboard."""
        if self.errors:
            return (
                f"CPRA sync finished with {len(self.errors)} error(s); "
                f"{self.files_ingested} file(s) ingested"
            )
        if not self.files_ingested:
            return "CPRA sync found no new records"
        return f"CPRA sync ingested {self.files_ingested} new file(s) from county productions"

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "campaign": (
                {
                    "id": self.campaign.id,
                    "number": self.campaign.number,
                    "title": self.campaign.title,
                    "state": self.campaign.state,
                    "proposed_targets": len(self.campaign.proposed_targets),
                }
                if self.campaign
                else None
            ),
            "targets_sent": self.targets_sent,
            "files_seen": self.files_seen,
            "files_skipped_not_importable": self.files_skipped_not_importable,
            "files_skipped_duplicate": self.files_skipped_duplicate,
            "files_ingested": self.files_ingested,
            "errors": list(self.errors),
            "notes": list(self.notes),
            "new_watermark": self.new_watermark.isoformat() if self.new_watermark else None,
            "succeeded": self.succeeded,
            "summary": self.summary(),
        }


def vault_provenance(vault_file: VaultFile) -> Provenance:
    """Provenance stamp recording the CPRA chain of custody for a file.

    Every PUR record extracted from this file inherits the stamp, so the public
    application page can say which agency produced the document and under which
    records request.
    """
    return Provenance(
        source_type=SourceType.CPRA_PRODUCTION,
        source_name=vault_file.filename,
        source_id=vault_file.request_number or vault_file.production or vault_file.id,
        extraction_method=ExtractionMethod.API,
        source_sha256=vault_file.sha256,
        locator=vault_file.relative_path,
        notes=tuple(
            note
            for note in (
                f"agency: {vault_file.agency}" if vault_file.agency else None,
                f"CPRA request: {vault_file.request_number}" if vault_file.request_number else None,
                f"production: {vault_file.production}" if vault_file.production else None,
                f"campaign: {vault_file.campaign}" if vault_file.campaign else None,
                f"received by Inquisitor: {vault_file.received_at.date().isoformat()}"
                if vault_file.received_at
                else None,
            )
            if note
        ),
    )


def run_monthly_sync(
    client: InquisitorClient,
    config: CpraSyncConfig,
    *,
    last_watermark: date | None,
    have_sha256: Callable[[str], bool],
    ingest: Callable[[SyncedFile], None],
    today: date | None = None,
) -> SyncResult:
    """Run one monthly CPRA refresh.

    ``have_sha256`` and ``ingest`` are injected so the job can be exercised
    without a database or a live Inquisitor instance: the first reports whether
    the tracker already holds a file, the second receives each new one.
    """
    now = datetime.now(UTC)
    today = today or now.date()

    if last_watermark is None:
        # First run: ask for the tracker's whole coverage period.
        window_start = config.coverage_start
    else:
        # Re-examine a short overlap so late-filed productions are not missed,
        # but never ask for records from before the coverage start.
        window_start = max(last_watermark - WATERMARK_OVERLAP, config.coverage_start)

    result = SyncResult(started_at=now, window_start=window_start, window_end=today)

    # --- 1. open this month's CPRA campaign -------------------------------
    if config.open_campaign:
        try:
            result.campaign = client.create_campaign(config.build_command(window_start, today))
            result.notes.append(
                f"opened CPRA campaign {result.campaign.number or result.campaign.id} "
                f"with {len(result.campaign.proposed_targets)} proposed target(s)"
            )
        except InquisitorError as exc:
            # A failed campaign must not stop us collecting what has already
            # been produced by earlier requests.
            result.errors.append(f"could not open CPRA campaign: {exc}")

        if config.auto_send and result.campaign:
            for target in result.campaign.proposed_targets:
                target_id = str(target.get("id") or target.get("target_id") or "")
                if not target_id:
                    continue
                try:
                    client.send_campaign_target(result.campaign.id, target_id)
                    result.targets_sent += 1
                except InquisitorError as exc:
                    result.errors.append(f"could not send campaign target {target_id}: {exc}")
        elif result.campaign and result.campaign.proposed_targets:
            result.notes.append(
                f"{len(result.campaign.proposed_targets)} target(s) are waiting for a person to "
                "approve sending (auto_send is off)"
            )

    # --- 2. collect what agencies have already produced -------------------
    agencies: Iterable[str | None] = config.agency_ids or (None,)
    seen_ids: set[str] = set()
    files: list[VaultFile] = []
    for agency_id in agencies:
        try:
            for vault_file in client.list_vault(date_from=window_start, agency_id=agency_id):
                if vault_file.id not in seen_ids:
                    seen_ids.add(vault_file.id)
                    files.append(vault_file)
        except InquisitorError as exc:
            result.errors.append(f"could not list the evidence vault: {exc}")
            return result

    result.files_seen = len(files)

    for vault_file in files:
        if not vault_file.looks_importable():
            result.files_skipped_not_importable += 1
            continue
        if vault_file.sha256 and have_sha256(vault_file.sha256):
            result.files_skipped_duplicate += 1
            continue
        try:
            content = client.download_original(vault_file.id)
        except InquisitorError as exc:
            result.errors.append(f"could not download {vault_file.filename}: {exc}")
            continue
        try:
            ingest(SyncedFile(vault_file, content, vault_provenance(vault_file)))
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the sync
            logger.exception("failed to ingest %s from Inquisitor", vault_file.filename)
            result.errors.append(f"could not ingest {vault_file.filename}: {exc}")
            continue
        result.files_ingested += 1

    # The watermark only moves when the whole run succeeded; otherwise the next
    # run re-examines this window rather than skipping a failed production.
    if result.succeeded:
        result.new_watermark = today
    else:
        result.notes.append(
            "watermark left unchanged because the run had errors; the next run will "
            "re-examine this window"
        )

    return result
