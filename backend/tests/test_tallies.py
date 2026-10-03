"""Yearly herbicide tallies: units kept apart, decimal slips held out."""

from __future__ import annotations

from app.api.tallies import Bucket, _Cluster, _hold_out_errors, _Line
from app.chemicals.scope import is_rodenticide
from app.core.units import normalize_quantity


def line(cid: int, product: str, *, gallons=None, pounds=None, acres=None,
         quantity=None, units=None) -> _Line:
    return _Line(cluster_id=cid, record_id=cid, kind="herbicide", label=product,
                 ingredients=(), gallons=gallons, pounds=pounds, quantity=quantity,
                 units=units, product=product, acres=acres, document_number=f"D{cid}",
                 date="2022-10-24")


def clusters(*ids: int) -> dict[int, _Cluster]:
    return {i: _Cluster(i, 2022, "Lassen", "lassen", "WM BEATY", "WM BEATY", 100.0)
            for i in ids}


def test_dropped_decimal_is_held_out_not_counted():
    # The real Lassen case: 29,025 lb of Velpar DF on 83 acres next to
    # the same crew's usual 3.5 lb an acre.
    lines = [
        line(1, "VELPAR DF", pounds=914.0, acres=261),
        line(2, "VELPAR DF", pounds=328.0, acres=81),
        line(3, "VELPAR DF", pounds=339.5, acres=97),
        line(4, "VELPAR DF", pounds=29025.0, acres=83),
    ]
    kept, held = _hold_out_errors(lines, clusters(1, 2, 3, 4))
    assert [k.cluster_id for k in kept] == [1, 2, 3]
    assert len(held) == 1
    assert held[0]["amount"] == 29025.0 and held[0]["rate"] > 300
    assert 3 < held[0]["typical_rate"] < 5


def test_too_few_lines_are_never_judged():
    lines = [line(1, "X", gallons=1.0, acres=10), line(2, "X", gallons=500.0, acres=10)]
    kept, held = _hold_out_errors(lines, clusters(1, 2))
    assert len(kept) == 2 and held == []


def test_gallons_and_pounds_never_added_and_unresolved_reported():
    bucket = Bucket()
    bucket.add_line(line(1, "A", gallons=10.0))
    bucket.add_line(line(1, "B", pounds=4.0))
    bucket.add_line(line(2, "C", quantity=12.0, units="Ounce"))
    out = bucket.to_dict()
    assert out["gallons"] == 10.0 and out["pounds"] == 4.0
    assert out["applications"] == 2
    assert out["unresolved"] == [{"unit": "ounce", "amount": 12.0, "lines": 1}]


def test_acres_counted_once_per_application():
    bucket = Bucket()
    bucket.add_application(1, 50.0)
    bucket.add_application(1, 50.0)
    bucket.add_application(2, 25.0)
    assert bucket.acres == 75.0


def test_rodent_baits_are_not_herbicides():
    assert is_rodenticide(["Strychnine"])
    assert is_rodenticide([], "GOPHER BAIT 50")
    assert not is_rodenticide(["Glyphosate"], "ACCORD XRT II")


def test_seed_oil_ounces_are_fluid_ounces():
    q = normalize_quantity(128.0, "Ounce", product_name="SUPER SPREAD MSO")
    assert q.gallons == 1.0
