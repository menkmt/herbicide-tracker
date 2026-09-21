import Link from "next/link";
import { type ApplicationRow, formatAcres, formatDateRange } from "@/lib/api";
import { FlagBadge } from "./Flags";

/**
 * The public application grid.
 *
 * One row is one logical application, however many underlying use reports it
 * was built from — that count is shown so the grouping is never hidden.
 */
export function ApplicationGrid({ rows }: { rows: ApplicationRow[] }) {
  if (rows.length === 0) {
    return <p className="muted">No applications match these filters.</p>;
  }
  return (
    <table className="grid-table">
      <thead>
        <tr>
          <th>Date</th>
          <th>Project / property</th>
          <th className="num">Acres</th>
          <th>Chemicals</th>
          <th>Method</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.slug} className={row.flag_level === "red" ? "flag-red" : undefined}>
            <td>
              <Link href={row.url}>
                {formatDateRange(row.date_start, row.date_end)}
                {row.is_planned && (
                  <div className="sub">
                    <span className="badge plain">Planned — notice of intent</span>
                  </div>
                )}
              </Link>
            </td>
            <td>
              <Link href={row.url}>
                <span className="title">{row.title}</span>
                {row.owner && row.owner !== row.title && (
                  <div className="sub">{row.owner}</div>
                )}
                {row.record_count > 1 && (
                  <div className="sub">
                    {row.record_count} pesticide use reports
                  </div>
                )}
              </Link>
            </td>
            <td className="num">
              <Link href={row.url}>{formatAcres(row.acres, row.acreage_is_partial)}</Link>
            </td>
            <td>
              <Link href={row.url}>
                {row.chemicals.slice(0, 3).join(", ")}
                {row.chemicals.length > 3 && ` +${row.chemicals.length - 3} more`}
                {row.flag_headline && (
                  <div className="sub">
                    <FlagBadge level={row.flag_level} headline={row.flag_headline} />
                  </div>
                )}
              </Link>
            </td>
            <td>
              <Link href={row.url}>
                <span className={`badge ${row.method === "aerial" ? "aerial" : "plain"}`}>
                  {row.method === "aerial" ? "Aerial" : row.method === "ground" ? "Ground" : "Unknown"}
                </span>
              </Link>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
