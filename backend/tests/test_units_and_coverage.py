"""Quantities, coverage window and site categories."""

from __future__ import annotations

from datetime import date

import pytest

from app.core.coverage import COVERAGE_START, DocumentKind, in_coverage
from app.core.site_category import SiteCategory, classify_site
from app.core.units import (
    QuantityTotal,
    UnitFamily,
    active_ingredient_pounds,
    normalize_quantity,
)


class TestQuantities:
    def test_gallons_and_pounds_convert(self):
        assert normalize_quantity(2, "Gallon").gallons == 2.0
        assert normalize_quantity(3, "Pounds").pounds == 3.0

    def test_an_ambiguous_ounce_is_not_guessed(self):
        """'Ounce' is fluid for a liquid and weight for a dry product."""
        quantity = normalize_quantity(12, "Ounce")
        assert quantity.family == UnitFamily.UNKNOWN
        assert "ambiguous" in (quantity.note or "")

    def test_formulation_resolves_an_ambiguous_ounce(self):
        dry = normalize_quantity(16, "Ounce", product_name="DU PONT VELPAR DF HERBICIDE")
        assert dry.pounds == pytest.approx(1.0)

        liquid = normalize_quantity(128, "Ounce", product_name="ALLIGARE ROTARY 2 SL")
        assert liquid.gallons == pytest.approx(1.0)

    def test_totals_keep_incompatible_units_apart(self):
        total = QuantityTotal()
        total.add(normalize_quantity(2, "Gallon"))
        total.add(normalize_quantity(3, "Pounds"))
        total.add(normalize_quantity(12, "Ounce"))
        data = total.to_dict()
        assert data["gallons"] == 2.0
        assert data["pounds"] == 3.0
        # The unresolvable ounces are reported, not absorbed into either total.
        assert data["unresolved"] == [{"amount": 12.0, "unit": "Ounce", "lines": 1}]

    def test_active_ingredient_pounds_from_a_dry_product(self):
        quantity = normalize_quantity(914, "Pounds", product_name="VELPAR DF")
        pounds, reason = active_ingredient_pounds(quantity, ingredient_percent=75.0)
        assert pounds == pytest.approx(685.5)
        assert "weight" in reason

    def test_a_liquid_without_a_density_reports_why_it_cannot_be_computed(self):
        quantity = normalize_quantity(2, "Gallon")
        pounds, reason = active_ingredient_pounds(quantity, ingredient_percent=41.0)
        assert pounds is None
        assert "density" in reason

    def test_an_incomplete_total_says_so(self):
        total = QuantityTotal()
        total.add_ai(None, "the product's active ingredients have not been identified yet")
        assert not total.ai_is_complete
        assert total.to_dict()["active_ingredient_gaps"]


class TestCoverage:
    def test_the_window_starts_in_2020(self):
        assert COVERAGE_START == date(2020, 1, 1)
        assert not in_coverage(date(2019, 12, 31))
        assert in_coverage(date(2020, 1, 1))

    def test_there_is_no_end_date(self):
        assert in_coverage(date(2099, 1, 1))

    def test_a_missing_date_is_not_in_coverage(self):
        assert not in_coverage(None)

    def test_a_notice_of_intent_is_not_a_reported_application(self):
        assert DocumentKind.NOTICE_OF_INTENT != DocumentKind.USE_REPORT
        assert "Planned" in DocumentKind.label(DocumentKind.NOTICE_OF_INTENT)


class TestSiteCategory:
    def test_the_dpr_forestry_code_is_authoritative(self):
        classified = classify_site("30000-0", "FOREST, TMBRLND")
        assert classified.category == SiteCategory.FORESTRY
        assert classified.confidence == "verified"
        assert classified.is_published

    def test_the_site_name_is_a_fallback(self):
        classified = classify_site(None, "FOREST, TMBRLND")
        assert classified.category == SiteCategory.FORESTRY
        assert classified.confidence == "high"

    @pytest.mark.parametrize(
        ("name", "category"),
        [
            ("RIGHTS OF WAY", SiteCategory.RIGHTS_OF_WAY),
            ("WATER AREA", SiteCategory.AQUATIC),
            ("NOXIOUS WEED CONTROL", SiteCategory.INVASIVE_PLANT),
        ],
    )
    def test_future_categories_are_classified_but_not_published(self, name, category):
        classified = classify_site(None, name)
        assert classified.category == category
        assert not classified.is_published
        assert classified.is_planned

    def test_an_unrecognised_site_is_held_rather_than_excluded(self):
        """A misclassified forestry record is one the public never sees."""
        classified = classify_site(None, "ALMOND")
        assert classified.category == SiteCategory.UNKNOWN
        assert not classified.is_published
        assert "review" in classified.reason
