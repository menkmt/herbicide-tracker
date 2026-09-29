"""The report-card rubric.

A county's grade is an editorial judgement by the publisher, and the site
says so wherever it appears. It is computed from two things the county itself
reported: how many priority applications happened, and how many of them the
county says it inspected. Everything here is deliberately simple enough to
print next to the grade.

"Priority" applications are the ones California's own enforcement guidance
singles out for use monitoring: restricted materials, and aerial application.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Share of priority applications with a use-monitoring inspection needed for
#: each grade. Anything below the last threshold with records in hand is an F.
GRADE_THRESHOLDS: tuple[tuple[str, float], ...] = (
    ("A", 0.50),
    ("B", 0.30),
    ("C", 0.15),
    ("D", 0.0001),
)

#: Statuses under which a grade may be computed at all. With anything else
#: the honest answer is "no records", not "F".
GRADABLE_STATUSES = frozenset({"received", "partial", "county_reports_none"})


@dataclass(frozen=True)
class YearMetrics:
    year: int
    applications: int
    priority_applications: int
    acres: float
    distinct_sites: int
    inspections: int
    use_monitoring_inspections: int
    #: Priority applications with a use-monitoring inspection matched to
    #: their site and dates.
    priority_inspected: int
    violations_found: int

    @property
    def coverage(self) -> float | None:
        """Share of priority applications inspected, or None if there were none."""
        if self.priority_applications == 0:
            return None
        return self.priority_inspected / self.priority_applications


@dataclass(frozen=True)
class Grade:
    letter: str | None
    coverage: float | None
    basis: str

    def to_dict(self) -> dict:
        return {"letter": self.letter, "coverage": self.coverage, "basis": self.basis}


def grade(metrics: YearMetrics | None, records_status: str) -> Grade:
    """Grade one year, or refuse to.

    ``records_status`` is the county's inspection-records status. A county
    whose records have not been obtained is not graded; a county that said it
    has no inspection records is graded on that statement.
    """
    if records_status not in GRADABLE_STATUSES:
        return Grade(None, None, _no_records_basis(records_status))
    if metrics is None or metrics.priority_applications == 0:
        return Grade(
            None,
            None,
            "No restricted-material or aerial applications were reported this year, "
            "so there was nothing to inspect.",
        )
    coverage = metrics.coverage or 0.0
    for letter, threshold in GRADE_THRESHOLDS:
        if coverage >= threshold:
            return Grade(letter, coverage, _basis(metrics, coverage, records_status))
    return Grade("F", coverage, _basis(metrics, coverage, records_status))


def _basis(m: YearMetrics, coverage: float, status: str) -> str:
    text = (
        f"{m.priority_inspected} of {m.priority_applications} restricted-material or "
        f"aerial applications ({coverage:.0%}) had a use-monitoring inspection recorded "
        f"by the county for the same site and dates."
    )
    if status == "partial":
        text += " The county's inspection records are incomplete, so this may understate them."
    if status == "county_reports_none":
        text += " The county stated it holds no inspection records for this period."
    return text


def _no_records_basis(status: str) -> str:
    return {
        "not_requested": "Inspection records have not been requested from this county yet.",
        "requested": "Inspection records have been requested and have not arrived.",
        "refused": "The county declined to provide inspection records.",
    }.get(status, "Inspection records for this county have not been obtained.")


def thresholds_text() -> str:
    parts = [f"{letter} at {threshold:.0%} or more" for letter, threshold in GRADE_THRESHOLDS[:-1]]
    return ", ".join(parts) + ", D for anything above zero, F for none."
