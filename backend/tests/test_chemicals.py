"""Chemical identification and the warning flag system."""

from __future__ import annotations

import pytest
import yaml

from app.chemicals.flags import (
    FlagLevel,
    FlagReason,
    flags_from_environmental_profile,
    flags_from_label,
    flags_from_permit_materials,
    flags_from_watchlist,
)
from app.chemicals.resolver import ProductResolver, Verification, load_watchlist
from app.core.provenance import ExtractionMethod, Provenance, SourceType

PERMIT_SOURCE = Provenance(
    source_type=SourceType.RESTRICTED_MATERIALS_PERMIT,
    source_name="Restricted Materials Permit 18",
    source_id="18-24-4500033",
    extraction_method=ExtractionMethod.DOCX_TABLE,
)
WATCHLIST_SOURCE = Provenance(
    source_type=SourceType.WATCHLIST,
    source_name="Protect Lassen watchlist",
    extraction_method=ExtractionMethod.HUMAN,
)
LABEL_SOURCE = Provenance(
    source_type=SourceType.PESTICIDE_LABEL,
    source_name="EPA registered label",
    extraction_method=ExtractionMethod.API,
)

PERMIT_MATERIALS = [
    {"number": "554", "name": "STRYCHNINE", "methods": "Ground"},
    {"number": "636", "name": "2,4-D", "methods": "Air/Ground"},
    {"number": "999", "name": "NON-RESTRICTED USE", "methods": "Aircraft"},
]


class TestRegulatoryFlags:
    def test_a_permit_establishes_california_restricted_status(self):
        """The county listing a material on a permit is why the permit exists."""
        flags = flags_from_permit_materials(PERMIT_MATERIALS, PERMIT_SOURCE)
        subjects = {f.subject for f in flags.flags}
        assert subjects == {"STRYCHNINE", "2,4-D"}
        assert all(f.is_regulatory for f in flags.flags)
        assert all(f.level == FlagLevel.RED for f in flags.flags)

    def test_the_non_restricted_placeholder_is_not_flagged(self):
        flags = flags_from_permit_materials(PERMIT_MATERIALS, PERMIT_SOURCE)
        assert "NON-RESTRICTED USE" not in {f.subject for f in flags.flags}

    def test_every_flag_cites_its_source(self):
        flags = flags_from_permit_materials(PERMIT_MATERIALS, PERMIT_SOURCE)
        for flag in flags.flags:
            assert "18-24-4500033" in flag.to_dict()["source_citation"]

    def test_federal_restricted_use_is_its_own_reason(self):
        flags = flags_from_label(
            subject="Some Product", signal_word=None,
            federal_restricted_use=True, provenance=LABEL_SOURCE,
        )
        assert flags.flags[0].reason == FlagReason.FEDERAL_RESTRICTED_USE


class TestWatchlistIsNotRegulation:
    """The watchlist is Protect Lassen's editorial judgement, not the law."""

    def test_watchlist_flags_are_marked_non_regulatory(self):
        flags = flags_from_watchlist(["Hexazinone"], load_watchlist(), WATCHLIST_SOURCE)
        assert len(flags.flags) == 1
        flag = flags.flags[0]
        assert flag.reason == FlagReason.WATCHLIST
        assert not flag.is_regulatory
        assert "Watchlist" in flag.label
        assert "Restricted" not in flag.label

    def test_a_chemical_can_carry_both_kinds_at_once(self):
        """2,4-D is genuinely both, and each flag names its own basis."""
        flags = flags_from_permit_materials(PERMIT_MATERIALS, PERMIT_SOURCE)
        flags.extend(flags_from_watchlist(["2,4-D"], load_watchlist(), WATCHLIST_SOURCE))
        for_24d = [f for f in flags.flags if f.subject == "2,4-D"]
        assert len(for_24d) == 2
        assert {f.is_regulatory for f in for_24d} == {True, False}

    def test_a_regulatory_restriction_is_the_headline(self):
        flags = flags_from_permit_materials(PERMIT_MATERIALS, PERMIT_SOURCE)
        flags.extend(flags_from_watchlist(["2,4-D"], load_watchlist(), WATCHLIST_SOURCE))
        assert flags.headline() == "RED — California Restricted Material"

    def test_a_watchlist_only_chemical_says_watchlist(self):
        flags = flags_from_watchlist(["Hexazinone"], load_watchlist(), WATCHLIST_SOURCE)
        assert flags.headline() == "RED — Protect Lassen Watchlist"
        assert not flags.regulatory_red

    def test_an_unlisted_chemical_gets_no_flag(self):
        assert not flags_from_watchlist(["Glyphosate"], load_watchlist(), WATCHLIST_SOURCE).flags

    def test_the_watchlist_is_data_not_code(self, tmp_path):
        """Protect Lassen must be able to change it without a deployment."""
        path = tmp_path / "watchlist.yml"
        path.write_text(
            yaml.safe_dump(
                {"active_ingredients": [{"name": "Glyphosate", "reason": "newly added"}]}
            )
        )
        custom = yaml.safe_load(path.read_text())
        flags = flags_from_watchlist(["Glyphosate"], custom, WATCHLIST_SOURCE)
        assert flags.flags[0].detail == "newly added"


class TestFlagLevels:
    def test_orange_flags_require_a_sourced_statement(self):
        empty = flags_from_environmental_profile(subject="X", provenance=LABEL_SOURCE)
        assert not empty.flags

        stated = flags_from_environmental_profile(
            subject="Hexazinone",
            provenance=LABEL_SOURCE,
            leaching_note="Agency finding about soil mobility.",
        )
        assert stated.flags[0].level == FlagLevel.ORANGE

    @pytest.mark.parametrize(
        ("word", "reason"),
        [("DANGER", FlagReason.SIGNAL_WORD_DANGER), ("WARNING", FlagReason.SIGNAL_WORD_WARNING)],
    )
    def test_signal_words_become_yellow_flags(self, word, reason):
        flags = flags_from_label(
            subject="X", signal_word=word, federal_restricted_use=False,
            provenance=LABEL_SOURCE,
        )
        assert flags.flags[0].reason == reason
        assert flags.flags[0].level == FlagLevel.YELLOW

    def test_red_outranks_orange_and_yellow(self):
        flags = flags_from_permit_materials(PERMIT_MATERIALS, PERMIT_SOURCE)
        flags.extend(
            flags_from_label(
                subject="2,4-D", signal_word="DANGER",
                federal_restricted_use=False, provenance=LABEL_SOURCE,
            )
        )
        assert flags.highest_level == FlagLevel.RED

    def test_duplicate_flags_are_collapsed(self):
        flags = flags_from_permit_materials(PERMIT_MATERIALS, PERMIT_SOURCE)
        flags.extend(flags_from_permit_materials(PERMIT_MATERIALS, PERMIT_SOURCE))
        assert len(flags.flags) == 2


class TestProductResolution:
    def test_a_seeded_product_resolves_to_its_ingredient(self):
        product = ProductResolver().resolve("352-581", "DU PONT VELPAR DF HERBICIDE")
        assert [i.name for i in product.ingredients] == ["Hexazinone"]

    def test_seed_data_is_below_the_bar_for_publication(self):
        """A starting value must be confirmed before anything derives from it."""
        product = ProductResolver().resolve("352-581", "VELPAR")
        assert product.verification == Verification.SEED
        assert not product.is_publishable

    def test_an_adjuvant_has_no_active_ingredient(self):
        """A spray additive must not be counted in chemical totals."""
        product = ProductResolver().resolve("2935-50176", "SUPER SPREAD MSO")
        assert product.is_adjuvant
        assert product.ingredients == []

    def test_an_unknown_product_is_reported_not_guessed(self):
        product = ProductResolver().resolve("99999-1", "MYSTERY PRODUCT")
        assert product.verification == Verification.UNRESOLVED
        assert not product.ingredients

    def test_a_provider_outranks_the_seed(self):
        from app.chemicals.resolver import IngredientRef, ResolvedProduct

        class FakeDpr:
            name = "California DPR product database"

            def lookup(self, base_reg_no, product_name):
                return ResolvedProduct(
                    base_epa_reg_no=base_reg_no,
                    name="VELPAR DF",
                    ingredients=[IngredientRef("Hexazinone", 75.0)],
                )

        product = ProductResolver(providers=[FakeDpr()]).resolve("352-581", "VELPAR")
        assert product.verification == Verification.VERIFIED
        assert product.is_publishable
        assert product.ingredients[0].percent == 75.0

    def test_a_missing_registration_number_resolves_to_nothing(self):
        product = ProductResolver().resolve(None, "SOME PRODUCT")
        assert product.verification == Verification.UNRESOLVED

    def test_resolution_reports_what_it_could_not_identify(self):
        report = ProductResolver().resolve_all(
            [("352-581", "VELPAR"), ("99999-1", "MYSTERY")]
        )
        assert "99999-1" in report.unresolved
        assert "352-581" in report.seed_only
