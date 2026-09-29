import Link from "next/link";
import type { GradeInfo, Official, RecordsStatus, ReportCard as ReportCardData } from "@/lib/api";

/**
 * A county's report card: what it was told about, against what it says it
 * inspected. Every number is the county's own; the grade is the publisher's
 * and is labelled as such wherever it appears.
 */

const STATUS_LABEL: Record<string, string> = {
  not_requested: "Not yet requested",
  requested: "Requested — not yet received",
  partial: "Partly received",
  received: "Received",
  county_reports_none: "County says it has none",
  refused: "County declined to provide",
};

export function GradeBadge({ grade, year }: { grade: GradeInfo; year?: number | null }) {
  if (!grade.letter) {
    return <span className="grade none" title={grade.basis}>No grade</span>;
  }
  return (
    <span className={`grade g-${grade.letter}`} title={grade.basis}>
      {grade.letter}
      {year && <small>{year}</small>}
    </span>
  );
}

export function Commissioner({ official, county }: { official: Official | null; county: string }) {
  if (!official) {
    return (
      <p className="muted small" style={{ margin: 0 }}>
        The current {county} County Agricultural Commissioner has not been recorded here yet.
      </p>
    );
  }
  return (
    <div>
      <div style={{ fontSize: "1.25rem", fontWeight: 700 }}>{official.name}</div>
      <div className="muted">
        {official.title}, {county} County
        {official.started_on && ` · since ${official.started_on.slice(0, 4)}`}
      </div>
      {(official.email || official.phone) && (
        <div className="small" style={{ marginTop: 6 }}>
          {official.email && <a href={`mailto:${official.email}`}>{official.email}</a>}
          {official.email && official.phone && " · "}
          {official.phone}
        </div>
      )}
      <div className="small muted" style={{ marginTop: 6 }}>
        {official.as_of && `As of ${official.as_of}. `}
        {official.source_url ? (
          <a href={official.source_url} rel="nofollow noopener">Source</a>
        ) : (
          official.source_note
        )}
      </div>
    </div>
  );
}

export function RecordsRow({ label, status }: { label: string; status: RecordsStatus }) {
  const ok = status.status === "received" || status.status === "partial";
  return (
    <tr>
      <td>{label}</td>
      <td>
        <span className={`badge ${ok ? "aerial" : status.status === "refused" ? "red" : "plain"}`}>
          {STATUS_LABEL[status.status] ?? status.status}
        </span>
      </td>
      <td className="small muted">
        {status.covers_from && status.covers_to && `${status.covers_from} to ${status.covers_to}`}
        {status.requested_on && !status.received_on && `requested ${status.requested_on}`}
        {status.note && ` — ${status.note}`}
      </td>
    </tr>
  );
}

export function ReportCardPanel({ card }: { card: ReportCardData }) {
  const county = card.county.name;
  const inspections = card.records.inspections;
  const graded = card.headline_grade.letter !== null;

  return (
    <section className="report-card">
      <div className="rc-head">
        <div>
          <span className="eyebrow" style={{ marginTop: 0 }}>Oversight report card</span>
          <h2 style={{ marginTop: 6 }}>{county} County Agricultural Commissioner</h2>
          <Commissioner official={card.officials.commissioner} county={county} />
        </div>
        <div className="rc-grade">
          <GradeBadge grade={card.headline_grade} year={card.headline_year} />
          <p className="small muted" style={{ maxWidth: "28ch", margin: "8px 0 0" }}>
            {card.headline_grade.basis}
          </p>
        </div>
      </div>

      <div className="cards stats" style={{ marginTop: 20 }}>
        <div className="card">
          <div className="n">{card.totals.applications.toLocaleString()}</div>
          <div className="k">Applications reported</div>
        </div>
        <div className="card">
          <div className="n">{Math.round(card.totals.acres).toLocaleString()}</div>
          <div className="k">Acres reported treated</div>
        </div>
        <div className="card">
          <div className="n">{card.totals.priority_applications.toLocaleString()}</div>
          <div className="k">Restricted or aerial</div>
        </div>
        <div className="card">
          <div className="n">
            {graded || inspections.status === "received" || inspections.status === "partial"
              ? card.totals.use_monitoring_inspections.toLocaleString()
              : "—"}
          </div>
          <div className="k">Use-monitoring inspections</div>
        </div>
      </div>

      {card.years.length > 0 && (
        <table className="records" style={{ marginTop: 18 }}>
          <thead>
            <tr>
              <th>Year</th>
              <th>Applications</th>
              <th>Sites</th>
              <th>Acres</th>
              <th>Restricted / aerial</th>
              <th>Inspected</th>
              <th>Coverage</th>
              <th>Violations found</th>
              <th>Grade</th>
            </tr>
          </thead>
          <tbody>
            {card.years.map((y) => (
              <tr key={y.year}>
                <td>{y.year}</td>
                <td>{y.applications}</td>
                <td>{y.distinct_sites}</td>
                <td>{Math.round(y.acres).toLocaleString()}</td>
                <td>{y.priority_applications}</td>
                <td>{y.grade.letter ? y.priority_inspected : "—"}</td>
                <td>{y.coverage !== null && y.grade.letter ? `${Math.round(y.coverage * 100)}%` : "—"}</td>
                <td>{y.grade.letter ? y.violations_found : "—"}</td>
                <td><GradeBadge grade={y.grade} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3>Records obtained from {county} County</h3>
      <table className="records">
        <thead><tr><th>Record type</th><th>Status</th><th></th></tr></thead>
        <tbody>
          <RecordsRow label="Pesticide use reports" status={card.records.use_reports} />
          <RecordsRow label="Notices of intent" status={card.records.notices_of_intent} />
          <RecordsRow label="Restricted materials permits" status={card.records.permits} />
          <RecordsRow label="Inspection records" status={card.records.inspections} />
          <RecordsRow label="Enforcement actions" status={card.records.enforcement} />
        </tbody>
      </table>

      <details className="legend panel" style={{ marginTop: 18 }}>
        <summary><strong>How the grade is worked out</strong></summary>
        <p className="small" style={{ marginTop: 12 }}>
          <strong>What counts:</strong> {card.rubric.priority}
        </p>
        <p className="small"><strong>What counts as inspected:</strong> {card.rubric.matched}</p>
        <p className="small"><strong>Thresholds:</strong> {card.rubric.thresholds}</p>
        <p className="small muted" style={{ marginBottom: 0 }}>
          {card.rubric.who} A county whose inspection records have not been obtained is not
          graded, because an absence of records is not evidence of an absence of inspections.
          Every figure comes from the county&rsquo;s own reports; if the county disputes one,{" "}
          <Link href="/contact">it can say so</Link>.
        </p>
      </details>
    </section>
  );
}
