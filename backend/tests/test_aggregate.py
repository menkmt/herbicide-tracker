"""Totals across applications."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.analytics.aggregate import aggregate, leaderboard, statewide_summary
from app.core.siteid import decode_site_id
from app.extraction.base import ApplicationMethod, ProductApplication, PurRecord


class FakeProduct:
    formulation = "DF"
    density_lb_per_gallon = None

    def ingredient_percentages(self):
        return [("Hexazinone", 75.0)]


def record(
    *, site="341003", day=1, acres=100.0, method=ApplicationMethod.GROUND,
    operator="WM BEATY AND ASSOC.", applicator="FOREST PROTECTION",
    product="TRANSLINE", quantity=2.0, units="Gallon", county="Lassen",
) -> PurRecord:
    value = PurRecord(
        document_number=f"DOC{site}{day}",
        county_name=county,
        operator_name=operator,
        applicator_name=applicator,
        site_id=site,
        site=decode_site_id(site, county="Lassen"),
        start_datetime=datetime(2024, 7, day, 8, tzinfo=UTC),
        application_date=datetime(2024, 7, day).date(),
        method=method,
        treated_amount=acres,
        commodity="FOREST, TMBRLND",
        commodity_code="30000-0",
    )
    value.products = [
        ProductApplication(
            product_name=product, epa_reg_no="352-581-AA",
            quantity=quantity, quantity_units=units, treated_amount=acres,
        )
    ]
    return value


class TestTotals:
    def test_acres_and_applications_total(self):
        totals = aggregate([record(acres=100), record(site="341004", acres=250)])[None]
        assert totals.applications == 2
        assert totals.acres_treated == 350.0

    def test_acreage_counts_once_per_application(self):
        """Three products on 100 acres is 100 acres, not 300."""
        value = record(acres=100)
        value.products = [
            ProductApplication(product_name=f"P{i}", quantity=1, quantity_units="Gallon",
                               treated_amount=100)
            for i in range(3)
        ]
        assert aggregate([value])[None].acres_treated == 100.0

    def test_aerial_and_ground_are_counted_separately(self):
        totals = aggregate(
            [record(), record(site="341004", method=ApplicationMethod.AERIAL)]
        )[None]
        assert totals.aerial_applications == 1
        assert totals.ground_applications == 1
        assert totals.aerial_share == 0.5

    def test_gallons_and_pounds_are_never_added_together(self):
        totals = aggregate(
            [
                record(quantity=2, units="Gallon"),
                record(site="341004", quantity=10, units="Pounds"),
            ]
        )[None]
        data = totals.quantity.to_dict()
        assert data["gallons"] == 2.0
        assert data["pounds"] == 10.0

    def test_an_unresolvable_unit_is_reported_separately(self):
        totals = aggregate([record(quantity=12, units="Ounce", product="UNKNOWN MIX")])[None]
        assert totals.quantity.to_dict()["unresolved"][0]["unit"] == "Ounce"

    def test_active_ingredient_totals_are_computed_when_possible(self):
        totals = aggregate(
            [record(quantity=100, units="Pounds", product="VELPAR DF")],
            lookup=lambda p: FakeProduct(),
        )[None]
        assert totals.quantity.ai_pounds == pytest.approx(75.0)
        assert "Hexazinone" in totals.by_ingredient

    def test_an_unidentified_product_makes_the_total_incomplete(self):
        totals = aggregate([record()])[None]
        assert not totals.quantity.ai_is_complete
        assert totals.quantity.to_dict()["active_ingredient_gaps"]

    def test_the_date_range_spans_every_record(self):
        totals = aggregate([record(day=1), record(site="341004", day=9)])[None]
        assert totals.first_date.day == 1
        assert totals.last_date.day == 9


class TestGrouping:
    def test_records_group_by_applicator(self):
        groups = aggregate(
            [
                record(applicator="PP Forestry LLC", acres=100),
                record(site="341004", applicator="PP Forestry LLC", acres=50),
                record(site="341005", applicator="WESTERN HELICOPTER SERVICES", acres=25),
            ],
            dimension="applicator",
        )
        assert groups["PP Forestry LLC"].acres_treated == 150.0
        assert groups["WESTERN HELICOPTER SERVICES"].applications == 1

    def test_a_record_contributes_to_every_product_it_applied(self):
        value = record()
        value.products = [
            ProductApplication(product_name="TRANSLINE", quantity=1, quantity_units="Gallon"),
            ProductApplication(product_name="SUPER SPREAD MSO", quantity=1,
                               quantity_units="Gallon"),
        ]
        groups = aggregate([value], dimension="product")
        assert set(groups) == {"TRANSLINE", "SUPER SPREAD MSO"}

    @pytest.mark.parametrize(
        "dimension", ["county", "year", "operator", "landowner", "method", "township"]
    )
    def test_every_dimension_groups(self, dimension):
        assert aggregate([record()], dimension=dimension)

    def test_the_leaderboard_sorts_by_metric(self):
        groups = aggregate(
            [
                record(applicator="A", acres=10),
                record(site="341004", applicator="B", acres=500),
            ],
            dimension="applicator",
        )
        assert leaderboard(groups)[0].key == "B"

    def test_the_statewide_summary_reports_the_forestry_share(self):
        summary = statewide_summary([record(), record(site="341004")])
        assert summary["forestry_share"] == 1.0
        assert summary["counties"][0]["label"] == "Lassen"
