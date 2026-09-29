from datetime import date

from app.reportcard.grading import Grade, YearMetrics, grade
from app.reportcard.inspections_csv import (
    classify_outcome,
    classify_type,
    match_headers,
    parse_date,
    read_inspections,
)


def metrics(priority: int, inspected: int) -> YearMetrics:
    return YearMetrics(
        year=2024, applications=priority + 3, priority_applications=priority, acres=1000.0,
        distinct_sites=priority + 2, inspections=inspected, use_monitoring_inspections=inspected,
        priority_inspected=inspected, violations_found=0,
    )


def test_no_records_means_no_grade_not_an_f():
    g = grade(metrics(20, 0), "not_requested")
    assert g.letter is None and "not been requested" in g.basis
    g = grade(metrics(20, 0), "requested")
    assert g.letter is None and "not arrived" in g.basis


def test_grade_thresholds():
    assert grade(metrics(20, 10), "received").letter == "A"
    assert grade(metrics(20, 6), "received").letter == "B"
    assert grade(metrics(20, 3), "received").letter == "C"
    assert grade(metrics(20, 1), "received").letter == "D"
    assert grade(metrics(20, 0), "received").letter == "F"


def test_county_that_says_it_has_no_records_is_graded_on_that():
    g = grade(metrics(12, 0), "county_reports_none")
    assert g.letter == "F" and "holds no inspection records" in g.basis


def test_nothing_to_inspect_is_not_graded():
    g = grade(metrics(0, 0), "received")
    assert g.letter is None and "nothing to inspect" in g.basis
    assert isinstance(g, Grade)


def test_header_matching_is_by_meaning():
    mapping, unmatched = match_headers(
        ["Insp Date", "Type", "Site No.", "Operator Name", "Result", "Weather"]
    )
    assert mapping["inspected_on"] == "Insp Date"
    assert mapping["site_id"] == "Site No."
    assert mapping["operator_name"] == "Operator Name"
    assert mapping["outcome"] == "Result"
    assert unmatched == ["Weather"]


def test_type_and_outcome_classification():
    assert classify_type("Pesticide Use Monitoring") == "use_monitoring"
    assert classify_type("Mix/Load") == "mix_load"
    assert classify_type("Records Insp.") == "records"
    assert classify_type("something else") == "other"
    assert classify_outcome("In compliance", None) == "in_compliance"
    assert classify_outcome("Violation - NOPA issued", None) == "violation"
    assert classify_outcome("whatever", 2) == "violation"
    assert classify_outcome(None, None) == "not_stated"


def test_dates_in_county_formats():
    assert parse_date("10/21/2024") == date(2024, 10, 21)
    assert parse_date("2024-10-21") == date(2024, 10, 21)
    assert parse_date("Oct 21, 2024") == date(2024, 10, 21)
    assert parse_date("yesterday") is None


def test_read_a_county_log():
    csv_text = (
        "Insp Date,Type,Site No.,Permit,Operator,Inspector,Result,Violations,Weather\n"
        "10/21/2024,Use Monitoring,29 11 36,18-24-4500033,RRF LASSEN PLUMAS LLC,J. Smith,"
        "In compliance,0,clear\n"
        "10/22/2024,Mix/Load,291136,18-24-4500033,RRF LASSEN PLUMAS LLC,J. Smith,Violation,1,\n"
        ",,,,,,,,\n"
    )
    result = read_inspections(csv_text)
    assert result.problems == []
    assert len(result.rows) == 2
    first, second = result.rows
    assert first.inspected_on == date(2024, 10, 21)
    assert first.inspection_type == "use_monitoring"
    assert first.site_id == "291136"
    assert first.outcome == "in_compliance"
    assert first.extra == {"Weather": "clear"}
    assert second.outcome == "violation" and second.violations_count == 1
    assert result.columns_unmatched == ["Weather"]
