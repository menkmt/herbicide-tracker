"""The site ID decoder is the tracker's geographic backbone.

Every case here comes from a real Lassen County document.
"""

from __future__ import annotations

import pytest

from app.core.confidence import Confidence
from app.core.siteid import (
    SiteIdDecodeError,
    cross_check,
    decode_site_id,
    decode_structured,
    parse_mtrs,
    sections_are_adjacent,
)


@pytest.mark.parametrize(
    ("raw", "expected", "description"),
    [
        ("291223", "M29N12E23", "T29N R12E Section 23"),
        ("341003", "M34N10E03", "T34N R10E Section 3"),
        ("351026", "M35N10E26", "T35N R10E Section 26"),
        ("280801", "M28N08E01", "T28N R8E Section 1"),
        ("29R1125", "M29N11E25", "T29N R11E Section 25"),
        ("T29N R12E Section 23", "M29N12E23", "T29N R12E Section 23"),
        ("M29N11E23", "M29N11E23", "T29N R11E Section 23"),
        ("29N 11E 23", "M29N11E23", "T29N R11E Section 23"),
    ],
)
def test_decodes_real_site_ids(raw, expected, description):
    decoded = decode_site_id(raw, county="Lassen")
    assert decoded.mtrs == expected
    assert decoded.legal_description == description


def test_explicit_directions_are_verified_but_inferred_ones_are_not():
    """A decode that had to assume a direction is not a direct reading."""
    explicit = decode_site_id("M29N11E23", county="Lassen")
    assert explicit.confidence == Confidence.VERIFIED
    assert explicit.inferred_fields == ()

    inferred = decode_site_id("291123", county="Lassen")
    assert inferred.confidence == Confidence.HIGH
    assert "township_dir" in inferred.inferred_fields
    assert inferred.warnings


def test_unknown_county_is_less_certain_than_a_configured_one():
    assert decode_site_id("291123").confidence == Confidence.MEDIUM


def test_ambiguous_meridian_county_is_never_high_confidence():
    """Siskiyou spans two meridians, so an assumed direction is a guess."""
    decoded = decode_site_id("291123", county="Siskiyou")
    assert decoded.confidence == Confidence.MEDIUM


@pytest.mark.parametrize(
    "raw", ["", "   ", "abcdef", "290000", "291299", "999999", "29Z1125"]
)
def test_rejects_undecodable_values(raw):
    with pytest.raises(SiteIdDecodeError):
        decode_site_id(raw, county="Lassen")


def test_section_must_be_within_a_township():
    with pytest.raises(SiteIdDecodeError, match="section 99"):
        decode_site_id("291299", county="Lassen")


def test_structured_columns_beat_a_packed_site_id():
    """DPR's own exports supply the directions, so nothing is inferred."""
    decoded = decode_structured(
        township="34", township_dir="N", range_="10", range_dir="E",
        section="03", meridian="M", county="Lassen",
    )
    assert decoded.mtrs == "M34N10E03"
    assert decoded.confidence == Confidence.VERIFIED


class TestMtrsFromScannedPermits:
    def test_parses_a_clean_mtrs(self):
        assert parse_mtrs("M28N08E01").mtrs == "M28N08E01"

    @pytest.mark.parametrize(
        ("scanned", "expected"),
        [("M28NO08E01", "M28N08E01"), ("M28NO9E06", "M28N09E06"), ("M28N O8E01", "M28N08E01")],
    )
    def test_repairs_ocr_letter_digit_confusion(self, scanned, expected):
        decoded = parse_mtrs(scanned)
        assert decoded.mtrs == expected
        # A repaired read is no longer a direct reading of the document.
        assert decoded.confidence == Confidence.HIGH
        assert any("OCR repair" in w for w in decoded.warnings)

    def test_refuses_nonsense(self):
        with pytest.raises(SiteIdDecodeError):
            parse_mtrs("not an mtrs")


class TestCrossCheck:
    """A site ID and a printed MTRS encode the same place by different routes."""

    def test_agreement_promotes_to_verified(self):
        result = cross_check("280801", "M28N08E01", county="Lassen")
        assert result.agrees
        assert result.decoded.confidence == Confidence.VERIFIED

    def test_agreement_repairs_a_damaged_scan(self):
        result = cross_check("280906", "M28NO9E06", county="Lassen")
        assert result.agrees
        assert result.decoded.mtrs == "M28N09E06"

    def test_disagreement_is_never_verified(self):
        result = cross_check("280801", "M28N09E01", county="Lassen")
        assert not result.agrees
        assert result.decoded.confidence == Confidence.MEDIUM
        assert "280801" in result.detail and "28N09E01" in result.detail

    def test_one_readable_source_is_not_corroboration(self):
        result = cross_check("280801", "garbage", county="Lassen")
        assert not result.agrees
        assert result.decoded.confidence != Confidence.VERIFIED

    def test_two_unreadable_sources_raise(self):
        with pytest.raises(SiteIdDecodeError):
            cross_check("nonsense", "garbage", county="Lassen")


class TestAdjacency:
    """Sections are numbered boustrophedonically, so adjacency needs the grid."""

    def test_neighbouring_sections_touch(self):
        a = decode_site_id("341003", county="Lassen")
        b = decode_site_id("341004", county="Lassen")
        assert sections_are_adjacent(a, b)

    def test_sections_in_the_same_column_touch(self):
        # Section 3 is directly above section 10 in the PLSS grid.
        a = decode_site_id("341003", county="Lassen")
        b = decode_site_id("341010", county="Lassen")
        assert sections_are_adjacent(a, b)

    def test_adjacency_crosses_a_township_boundary(self):
        # Section 31 is the south-west corner; section 6 of the township below
        # is directly beneath it.
        a = decode_site_id("341031", county="Lassen")
        b = decode_site_id("331006", county="Lassen")
        assert sections_are_adjacent(a, b)

    def test_distant_sections_do_not_touch(self):
        a = decode_site_id("341003", county="Lassen")
        b = decode_site_id("351026", county="Lassen")
        assert not sections_are_adjacent(a, b)
