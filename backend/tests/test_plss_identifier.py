from app.core.siteid import parse_mtrs
from app.providers.plss import frstdivid


def test_frstdivid_matches_cadnsdi_layout():
    # T29N R12E section 23, Mount Diablo meridian.
    assert frstdivid(parse_mtrs("M29N12E23")) == "CA210290N0120E0SN230"


def test_single_digit_section_and_range_are_zero_padded():
    assert frstdivid(parse_mtrs("M28N08E01")) == "CA210280N0080E0SN010"


def test_humboldt_and_san_bernardino_meridians():
    assert frstdivid(parse_mtrs("H05N02E10")).startswith("CA15")
    assert frstdivid(parse_mtrs("S02S03W05")).startswith("CA27")
