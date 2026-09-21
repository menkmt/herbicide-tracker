"""Grouping PUR records into the applications the public sees."""

from __future__ import annotations

from datetime import UTC, datetime

from app.clustering.cluster import cluster_records
from app.clustering.score import DEFAULT_WEIGHTS, Outcome, score_pair
from app.core.siteid import decode_site_id
from app.extraction.base import ApplicationMethod, ProductApplication, PurRecord


def make_record(
    *,
    document: str,
    site: str,
    day: int,
    month: int = 7,
    year: int = 2021,
    operator: str = "WM BEATY AND ASSOC.",
    applicator: str = "FOREST PROTECTION",
    permit: str | None = "4500033",
    products: tuple[str, ...] = ("62719-259",),
    acres: float = 100.0,
    method: str = ApplicationMethod.GROUND,
) -> PurRecord:
    record = PurRecord(
        document_number=document,
        permit_number=permit,
        operator_name=operator,
        applicator_name=applicator,
        site_id=site,
        site=decode_site_id(site, county="Lassen"),
        start_datetime=datetime(year, month, day, 8, 0, tzinfo=UTC),
        application_date=datetime(year, month, day).date(),
        method=method,
        treated_amount=acres,
        commodity="FOREST, TMBRLND",
        commodity_code="30000-0",
    )
    record.products = [
        ProductApplication(product_name=f"PRODUCT {reg}", epa_reg_no=reg, quantity=1.0)
        for reg in products
    ]
    return record


class TestPairScoring:
    def test_same_day_adjacent_sections_cluster_automatically(self):
        score = score_pair(
            make_record(document="A", site="341003", day=1),
            make_record(document="B", site="341004", day=1),
        )
        assert score.outcome == Outcome.AUTO
        assert score.total >= DEFAULT_WEIGHTS.auto_threshold
        assert "same property owner" in score.explain()

    def test_a_score_explains_itself(self):
        score = score_pair(
            make_record(document="A", site="341003", day=1),
            make_record(document="B", site="341004", day=1),
        )
        matched = {s.name for s in score.matched_signals}
        assert {"same property owner", "application dates", "same permit number"} <= matched

    def test_shared_attributes_alone_do_not_cluster_across_months(self):
        """One operator's whole year shares owner, permit and applicator.

        Those are constants for that operator and say nothing about which
        records belong together, so without temporal proximity they must not
        fuse into one application.
        """
        score = score_pair(
            make_record(document="A", site="341003", month=5, day=1),
            make_record(document="B", site="341004", month=10, day=1),
        )
        assert score.total >= DEFAULT_WEIGHTS.auto_threshold
        assert score.outcome != Outcome.AUTO
        assert any("not close in time" in s.detail for s in score.signals)

    def test_records_a_week_apart_are_proposed_not_merged(self):
        score = score_pair(
            make_record(document="A", site="341003", day=1),
            make_record(document="B", site="341004", day=9),
        )
        assert score.outcome == Outcome.REVIEW

    def test_different_operators_do_not_cluster(self):
        score = score_pair(
            make_record(document="A", site="341003", day=1),
            make_record(
                document="B", site="341004", day=1,
                operator="RRF Lassen-Plumas LLC", permit="9900001",
                applicator="PP Forestry LLC", products=("524-529",),
            ),
        )
        assert score.outcome == Outcome.SEPARATE

    def test_owner_spelling_variation_still_matches(self):
        score = score_pair(
            make_record(document="A", site="341003", day=1, operator="WM BEATY AND ASSOC."),
            make_record(
                document="B", site="341004", day=1, operator="W.M. Beaty & Associates, Inc."
            ),
        )
        assert score.outcome == Outcome.AUTO

    def test_weights_are_configurable(self):
        from dataclasses import replace

        weights = replace(DEFAULT_WEIGHTS, same_owner=0, auto_threshold=200)
        score = score_pair(
            make_record(document="A", site="341003", day=1),
            make_record(document="B", site="341004", day=1),
            weights=weights,
        )
        assert score.outcome != Outcome.AUTO


class TestClustering:
    def test_a_multi_site_project_becomes_one_application(self):
        """Ten site IDs treated over three days are one project."""
        records = [
            make_record(document=f"DOC{i}", site=site, day=day)
            for i, (site, day) in enumerate(
                [
                    ("341003", 1), ("341004", 1), ("341005", 1), ("341009", 1),
                    ("341010", 1), ("351026", 2), ("351027", 2), ("351029", 3),
                ]
            )
        ]
        result = cluster_records(records)
        assert len(result.clusters) == 1
        cluster = result.clusters[0]
        assert cluster.record_count == 8
        assert len(cluster.site_ids) == 8
        assert cluster.total_acres == 800.0

    def test_separate_campaigns_stay_separate(self):
        records = [
            make_record(document="A", site="341003", month=5, day=1),
            make_record(document="B", site="341004", month=5, day=2),
            make_record(document="C", site="341009", month=10, day=1),
            make_record(document="D", site="341010", month=10, day=2),
        ]
        result = cluster_records(records)
        assert len(result.clusters) == 2

    def test_a_lone_record_is_still_an_application(self):
        result = cluster_records([make_record(document="A", site="341003", day=1)])
        assert len(result.clusters) == 1
        assert result.clusters[0].confidence == "verified"

    def test_source_records_are_never_modified(self):
        records = [
            make_record(document="A", site="341003", day=1),
            make_record(document="B", site="341004", day=1),
        ]
        before = [r.to_dict() for r in records]
        cluster_records(records)
        assert [r.to_dict() for r in records] == before

    def test_acreage_counts_once_per_application_not_per_product(self):
        """A three-product tank mix on 100 acres is 100 acres."""
        record = make_record(
            document="A", site="341003", day=1,
            products=("62719-259", "2935-50176", "524-529"), acres=100.0,
        )
        cluster = cluster_records([record]).clusters[0]
        assert cluster.total_acres == 100.0

    def test_an_aerial_record_makes_the_project_aerial(self):
        records = [
            make_record(document="A", site="341003", day=1),
            make_record(document="B", site="341004", day=1, method=ApplicationMethod.AERIAL),
        ]
        cluster = cluster_records(records).clusters[0]
        assert cluster.method == ApplicationMethod.AERIAL
        assert cluster.is_mixed_method
        assert cluster.needs_review

    def test_the_title_falls_back_to_the_owner_and_says_so(self):
        cluster = cluster_records([make_record(document="A", site="341003", day=1)]).clusters[0]
        assert cluster.title() == "WM BEATY AND ASSOC."
        assert "property owner" in cluster.title_basis

    def test_an_official_project_name_outranks_the_owner(self):
        cluster = cluster_records([make_record(document="A", site="341003", day=1)]).clusters[0]
        cluster.project_name = "Motor Sheep Biomass"
        cluster.project_source = "CAL FIRE THP 2-24-001-LAS"
        assert cluster.title() == "Motor Sheep Biomass"

    def test_an_administrator_override_outranks_everything(self):
        cluster = cluster_records([make_record(document="A", site="341003", day=1)]).clusters[0]
        cluster.project_name = "Motor Sheep Biomass"
        cluster.title_override = "Motor Sheep Biomass (October 2024)"
        assert cluster.title() == "Motor Sheep Biomass (October 2024)"

    def test_no_name_is_ever_invented(self):
        record = make_record(document="A", site="341003", day=1, operator="")
        record.operator_name = None
        cluster = cluster_records([record]).clusters[0]
        assert cluster.title().startswith("Application at")
        assert "no owner or project name" in cluster.title_basis

    def test_empty_input_is_handled(self):
        assert cluster_records([]).clusters == []
