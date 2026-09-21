"""Parcel matching, access tiers and the monthly CPRA sync."""

from __future__ import annotations

from datetime import date

import pytest

from app.core.access import ANONYMOUS, AccessDenied, Capability, Principal, Tier, clamp_page_size, require
from app.jobs.inquisitor_sync import CpraSyncConfig, run_monthly_sync, vault_provenance
from app.pipeline.parcels_stage import ParcelOutcome, association_note, select_parcels
from app.providers.inquisitor import Campaign, InquisitorError, VaultFile
from app.providers.parcels.base import ParcelRecord

BEATY = "WM BEATY AND ASSOC."


def parcel(apn: str, owner: str, acres: float = 640.0) -> ParcelRecord:
    return ParcelRecord(apn=apn, owner=owner, acreage=acres, source="test")


class TestParcelMatching:
    def test_parcels_recorded_to_the_operator_are_selected(self):
        candidates = [
            parcel("001-010-001", "W M BEATY & ASSOCIATES INC"),
            parcel("001-010-002", "WM BEATY AND ASSOC.", 320),
            parcel("001-010-003", "UNITED STATES OF AMERICA"),
        ]
        selection = select_parcels(candidates, BEATY, reported_acres=407)
        assert selection.outcome == ParcelOutcome.MATCHED
        assert selection.apns == ["001-010-001", "001-010-002"]

    def test_the_detail_compares_property_acres_with_reported_acres(self):
        """A 960-acre property behind 407 treated acres must not read as equal."""
        selection = select_parcels(
            [parcel("a", BEATY, 640), parcel("b", BEATY, 320)], BEATY, reported_acres=407
        )
        assert "960" in selection.detail and "407" in selection.detail

    def test_a_near_miss_goes_to_review_rather_than_being_selected(self):
        selection = select_parcels([parcel("x", "BEATY TIMBER HOLDINGS LLC")], BEATY)
        assert selection.needs_review
        assert selection.outcome == ParcelOutcome.NEEDS_REVIEW

    def test_no_owner_match_is_reported_with_its_reason(self):
        selection = select_parcels([parcel("x", "UNITED STATES OF AMERICA")], BEATY)
        assert selection.review_reason == "no_parcel_owner_match"
        assert selection.apns == []

    def test_no_candidates_is_distinguished_from_no_match(self):
        assert select_parcels([], BEATY).outcome == ParcelOutcome.NO_CANDIDATES

    def test_a_record_with_no_operator_cannot_be_matched(self):
        selection = select_parcels([parcel("x", "ANYONE")], None)
        assert selection.review_reason == "missing_owner"

    def test_the_landowner_field_is_tried_as_well_as_the_permittee(self):
        """Some counties name the property owner separately from the operator."""
        selection = select_parcels(
            [parcel("x", "Terry Barr")], "Mike's Land Mgmt", landowner="Terry Barr"
        )
        assert selection.outcome == ParcelOutcome.MATCHED

    def test_the_public_note_never_claims_the_parcel_was_sprayed(self):
        selection = select_parcels([parcel("a", BEATY, 640)], BEATY, reported_acres=63)
        note = association_note(selection, 63)
        assert "not the area actually sprayed" in note
        assert "63 treated acres" in note

    def test_an_unmatched_application_says_so_plainly(self):
        assert "No parcel has been matched" in association_note(select_parcels([], BEATY), None)


class TestAccessTiers:
    def test_the_public_tier_can_read_and_search(self):
        assert ANONYMOUS.can(Capability.READ_PUBLIC)
        assert ANONYMOUS.can(Capability.RADIUS_SEARCH)

    def test_bulk_and_aggregate_access_require_a_key(self):
        assert not ANONYMOUS.can(Capability.BULK_EXPORT)
        assert not ANONYMOUS.can(Capability.READ_AGGREGATES)

    def test_a_subscriber_gets_bulk_and_aggregates(self):
        subscriber = Principal(tier=Tier.SUBSCRIBER)
        assert subscriber.can(Capability.BULK_EXPORT)
        assert subscriber.can(Capability.READ_AGGREGATES)

    def test_only_an_administrator_can_see_unpublished_records(self):
        assert not Principal(tier=Tier.SUBSCRIBER).can(Capability.READ_UNPUBLISHED)
        assert Principal(tier=Tier.ADMIN).can(Capability.READ_UNPUBLISHED)

    def test_page_size_is_capped_by_tier(self):
        assert clamp_page_size(ANONYMOUS, 5000, public=50, subscriber=500) == 50
        assert clamp_page_size(Principal(tier=Tier.SUBSCRIBER), 5000,
                               public=50, subscriber=500) == 500

    def test_denial_explains_the_upgrade_path(self):
        with pytest.raises(AccessDenied, match="API key"):
            require(ANONYMOUS, Capability.READ_AGGREGATES)


class FakeInquisitor:
    """Stands in for the Inquisitor workstation API."""

    def __init__(self, files: list[VaultFile], *, fail_download: str | None = None) -> None:
        self._files = files
        self._fail_download = fail_download
        self.created_command: str | None = None
        self.sent: list[str] = []

    def create_campaign(self, command: str) -> Campaign:
        self.created_command = command
        return Campaign(
            id="camp-1", number="C-2026-09", title="Monthly CPRA", state="draft",
            proposed_targets=[{"id": "t1"}, {"id": "t2"}],
        )

    def send_campaign_target(self, campaign_id: str, target_id: str) -> dict:
        self.sent.append(target_id)
        return {}

    def list_vault(self, **kwargs):
        self.last_query = kwargs
        return list(self._files)

    def download_original(self, source_id: str) -> bytes:
        if source_id == self._fail_download:
            raise InquisitorError("download failed")
        return b"file contents"


def vault_file(source_id: str, name: str, sha: str) -> VaultFile:
    return VaultFile(
        id=source_id, filename=name, sha256=sha, byte_size=10, mime_type="text/csv",
        file_type="csv", received_at=None, processing_outcome="complete",
        agency="Lassen County Department of Agriculture",
        request_number="CPRA-2026-014", production="P-1",
    )


class TestMonthlyCpraSync:
    def test_a_first_run_asks_for_the_whole_coverage_period(self):
        client = FakeInquisitor([])
        result = run_monthly_sync(
            client, CpraSyncConfig(), last_watermark=None,
            have_sha256=lambda s: False, ingest=lambda f: None,
            today=date(2026, 9, 21),
        )
        assert result.window_start == date(2020, 1, 1)
        assert "2020-01-01" in client.created_command

    def test_the_request_names_all_three_document_kinds(self):
        client = FakeInquisitor([])
        run_monthly_sync(
            client, CpraSyncConfig(), last_watermark=None,
            have_sha256=lambda s: False, ingest=lambda f: None,
        )
        command = client.created_command.lower()
        assert "pesticide use report" in command
        assert "notices of intent" in command
        assert "restricted materials permit" in command

    def test_a_later_run_overlaps_but_never_predates_coverage(self):
        client = FakeInquisitor([])
        result = run_monthly_sync(
            client, CpraSyncConfig(), last_watermark=date(2020, 1, 5),
            have_sha256=lambda s: False, ingest=lambda f: None,
            today=date(2026, 9, 21),
        )
        assert result.window_start == date(2020, 1, 1)

    def test_new_files_are_ingested_and_carry_their_cpra_provenance(self):
        ingested = []
        client = FakeInquisitor([vault_file("s1", "records.csv", "abc")])
        result = run_monthly_sync(
            client, CpraSyncConfig(), last_watermark=date(2026, 8, 1),
            have_sha256=lambda s: False, ingest=ingested.append,
        )
        assert result.files_ingested == 1
        provenance = ingested[0].provenance
        assert provenance.source_id == "CPRA-2026-014"
        assert any("Lassen County" in n for n in provenance.notes)

    def test_files_already_held_are_skipped_by_hash(self):
        client = FakeInquisitor([vault_file("s1", "records.csv", "abc")])
        result = run_monthly_sync(
            client, CpraSyncConfig(), last_watermark=None,
            have_sha256=lambda s: s == "abc", ingest=lambda f: None,
        )
        assert result.files_skipped_duplicate == 1
        assert result.files_ingested == 0

    def test_irrelevant_file_types_are_not_downloaded(self):
        image = VaultFile(
            id="s2", filename="photo.jpg", sha256="d", byte_size=1, mime_type="image/jpeg",
            file_type="jpg", received_at=None, processing_outcome="complete",
        )
        result = run_monthly_sync(
            FakeInquisitor([image]), CpraSyncConfig(), last_watermark=None,
            have_sha256=lambda s: False, ingest=lambda f: None,
        )
        assert result.files_skipped_not_importable == 1

    def test_sending_records_requests_is_off_by_default(self):
        """A CPRA request is correspondence from the publisher."""
        client = FakeInquisitor([])
        result = run_monthly_sync(
            client, CpraSyncConfig(), last_watermark=None,
            have_sha256=lambda s: False, ingest=lambda f: None,
        )
        assert client.sent == []
        assert result.targets_sent == 0
        assert any("waiting for a person" in note for note in result.notes)

    def test_sending_can_be_enabled_explicitly(self):
        client = FakeInquisitor([])
        result = run_monthly_sync(
            client, CpraSyncConfig(auto_send=True), last_watermark=None,
            have_sha256=lambda s: False, ingest=lambda f: None,
        )
        assert client.sent == ["t1", "t2"]
        assert result.targets_sent == 2

    def test_the_watermark_does_not_advance_after_a_failure(self):
        """Otherwise the next run silently skips the failed production."""
        client = FakeInquisitor(
            [vault_file("s1", "a.csv", "a"), vault_file("s2", "b.csv", "b")],
            fail_download="s1",
        )
        result = run_monthly_sync(
            client, CpraSyncConfig(), last_watermark=date(2026, 8, 1),
            have_sha256=lambda s: False, ingest=lambda f: None,
            today=date(2026, 9, 21),
        )
        assert result.files_ingested == 1
        assert result.errors
        assert result.new_watermark is None

    def test_a_clean_run_advances_the_watermark(self):
        result = run_monthly_sync(
            FakeInquisitor([vault_file("s1", "a.csv", "a")]), CpraSyncConfig(),
            last_watermark=date(2026, 8, 1), have_sha256=lambda s: False,
            ingest=lambda f: None, today=date(2026, 9, 21),
        )
        assert result.new_watermark == date(2026, 9, 21)

    def test_one_bad_file_does_not_stop_the_rest(self):
        def ingest(synced):
            if synced.vault_file.id == "s1":
                raise ValueError("corrupt")

        result = run_monthly_sync(
            FakeInquisitor([vault_file("s1", "a.csv", "a"), vault_file("s2", "b.csv", "b")]),
            CpraSyncConfig(), last_watermark=None, have_sha256=lambda s: False, ingest=ingest,
        )
        assert result.files_ingested == 1
        assert len(result.errors) == 1

    def test_a_file_carries_a_readable_citation(self):
        provenance = vault_provenance(vault_file("s1", "records.csv", "abc"))
        assert "records.csv" in provenance.describe()
        assert "CPRA-2026-014" in provenance.describe()
