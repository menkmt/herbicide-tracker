import Link from "next/link";
import { GradeBadge } from "@/components/ReportCard";
import { api } from "@/lib/api";

export const metadata = {
  title: "County agricultural commissioner report card — pesticide oversight in California",
  description:
    "How many forestry pesticide applications each California county was told about, and how " +
    "many its agricultural commissioner says it inspected. Graded county by county from the " +
    "counties' own records.",
  alternates: { canonical: "/report-card" },
};

const STATUS_SHORT: Record<string, string> = {
  not_requested: "not requested",
  requested: "requested",
  partial: "partly received",
  received: "received",
  county_reports_none: "county says none",
  refused: "refused",
};

export default async function StatewideReportCardPage() {
  const data = await api.statewideReportCard().catch(() => ({ counties: [], rubric_thresholds: "" }));

  return (
    <>
      <span className="eyebrow">Statewide · Oversight</span>
      <h1>
        Who is checking? <span className="gradient-text">County by county.</span>
      </h1>
      <p className="lede">
        Every commercial pesticide application in California is reported to a county
        agricultural commissioner, whose office is meant to inspect applications of
        restricted materials as they happen. This page puts the two numbers side by side —
        what each county was told about, and what its commissioner says the county
        inspected — using the county&rsquo;s own records for both. Worst first.
      </p>

      {data.counties.length === 0 ? (
        <p className="muted">No county has enough published records to show yet.</p>
      ) : (
        <table className="records rc-table">
          <thead>
            <tr>
              <th>Grade</th>
              <th>County</th>
              <th>Commissioner</th>
              <th>Applications</th>
              <th>Restricted / aerial</th>
              <th>Inspected</th>
              <th>Inspection records</th>
            </tr>
          </thead>
          <tbody>
            {data.counties.map((c) => (
              <tr key={c.county.slug}>
                <td><GradeBadge grade={c.headline_grade} year={c.headline_year} /></td>
                <td>
                  <Link href={`/applications/${c.county.slug}#report-card`}>
                    <strong>{c.county.name}</strong>
                  </Link>
                </td>
                <td>
                  {c.commissioner ? c.commissioner.name : <span className="muted">not recorded</span>}
                </td>
                <td>{c.totals.applications.toLocaleString()}</td>
                <td>{c.totals.priority_applications.toLocaleString()}</td>
                <td>
                  {c.headline_grade.letter
                    ? c.totals.priority_inspected.toLocaleString()
                    : <span className="muted">—</span>}
                </td>
                <td className="small muted">{STATUS_SHORT[c.records.status] ?? c.records.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <p className="small muted" style={{ marginTop: 18 }}>
        Grades: {data.rubric_thresholds} A county whose inspection records have not been
        obtained is not graded. The grade is this publisher&rsquo;s editorial rating, not a
        regulatory finding. See any county&rsquo;s page for the year-by-year figures and how
        each one was matched.
      </p>
    </>
  );
}
