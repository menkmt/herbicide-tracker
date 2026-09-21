import type { FlagSummary } from "@/lib/api";

/**
 * Warning badge for a grid row.
 *
 * The label always states its own reason — "California Restricted Material"
 * or "Editorial Watchlist" — because a watchlist entry is Protect
 * Lassen's editorial judgement and must never read as a legal restriction.
 */
export function FlagBadge({
  level,
  headline,
}: {
  level: string | null;
  headline: string | null;
}) {
  if (!level || !headline) return null;
  const text = headline.replace(/^(RED|ORANGE|YELLOW)\s*—\s*/, "");
  return <span className={`badge ${level}`}>{text}</span>;
}

export function FlagList({ flags }: { flags: FlagSummary | null }) {
  if (!flags || flags.flags.length === 0) {
    return (
      <p className="muted small">
        No restricted-material or watchlist flags are recorded for the products on
        this application.
      </p>
    );
  }
  return (
    <div>
      {flags.flags.map((flag, index) => (
        <div key={index} className={`flag-block ${flag.level}`}>
          <strong>{flag.label}</strong>
          <div>{flag.detail}</div>
          <div className="src">
            {flag.is_regulatory
              ? "Regulatory status, from: "
              : "Editorial flag, from: "}
            {flag.source_citation}
          </div>
        </div>
      ))}
    </div>
  );
}
