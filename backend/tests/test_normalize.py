"""Name resolution. Every spelling here appears in a real source document."""

from __future__ import annotations

import pytest

from app.core.normalize import (
    company_key,
    compare_companies,
    compare_people,
    normalize_person,
)

BEATY_SPELLINGS = [
    "W.M. BEATY & ASSOCIATES, INC.",
    "WM BEATY AND ASSOC.",
    "W M Beaty & Associates",
    "wm beaty and associates inc",
]


@pytest.mark.parametrize("spelling", BEATY_SPELLINGS)
def test_all_beaty_spellings_share_one_key(spelling):
    assert company_key(spelling) == company_key(BEATY_SPELLINGS[0])


@pytest.mark.parametrize(
    "spelling",
    ["RRF LASSEN-PLUMAS LLC", "RRF Lassen Plumas LLC", "R.R.F. Lassen-Plumas, LLC"],
)
def test_punctuation_and_legal_form_do_not_change_identity(spelling):
    assert company_key(spelling) == "RRF LASSEN PLUMAS"


def test_abbreviated_name_matches_its_full_form():
    match = compare_companies("WM Beaty", "W M Beaty & Associates")
    assert match.matched
    assert match.score >= 0.9


def test_abbreviation_matching_requires_the_leading_word_to_agree():
    """'Beaty Forest Products' must not absorb 'WM Beaty'."""
    assert not compare_companies("Beaty Forest Products", "WM Beaty").matched


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("PP Forestry LLC", "FOREST PROTECTION"),
        ("WM BEATY AND ASSOC.", "RRF Lassen-Plumas LLC"),
        ("WESTERN HELICOPTER SERVICES", "PP Forestry LLC"),
    ],
)
def test_unrelated_businesses_do_not_match(left, right):
    assert not compare_companies(left, right).matched


def test_abbreviated_words_are_stemmed():
    assert compare_companies("Western Helicopter Svcs Inc", "WESTERN HELICOPTER SERVICES").matched


def test_empty_names_never_match():
    assert not compare_companies(None, "WM BEATY").matched
    assert not compare_companies("", "").matched


class TestPersonNames:
    @pytest.mark.parametrize(
        ("written", "key"),
        [
            ("Compton, Shane", "COMPTON|SHANE"),
            ("Shane Compton", "COMPTON|SHANE"),
            ("Scott P. Carnegie", "CARNEGIE|SCOTT"),
            ("Carnegie, Scott", "CARNEGIE|SCOTT"),
        ],
    )
    def test_both_name_orders_resolve_together(self, written, key):
        assert normalize_person(written).key == key

    def test_suffixes_are_separated(self):
        parsed = normalize_person("John Smith Jr")
        assert parsed.family == "SMITH"
        assert parsed.suffix == "JR"

    def test_matching_people_is_a_candidate_not_a_decision(self):
        """Merging two real people is a factual error, so names alone never suffice."""
        match = compare_people("Compton, Shane", "Shane Compton")
        assert match.matched
        assert "corroborate" in match.reason

    def test_different_middle_names_block_a_match(self):
        match = compare_people("Scott P. Carnegie", "Scott R. Carnegie")
        assert not match.matched

    def test_shared_surname_is_not_a_match(self):
        assert not compare_people("Shane Compton", "Gary Compton").matched
