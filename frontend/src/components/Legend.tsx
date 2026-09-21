/**
 * Colour key.
 *
 * The site uses colour to mean exactly one thing — a warning about a chemical —
 * and two of those warnings are red for different reasons. A reader who sees a
 * red row has no way of knowing whether that is a matter of law or this
 * publisher's own editorial judgement unless the site says so plainly, so the
 * key is shown wherever colour appears rather than hidden on an About page.
 */

const PUBLISHER = process.env.NEXT_PUBLIC_PUBLISHER_NAME ?? "Protect Lassen";

export function Legend({ compact = false }: { compact?: boolean }) {
  return (
    <details className="legend panel" open={!compact}>
      <summary>
        <strong>What the colours mean</strong>
      </summary>

      <div className="legend-grid">
        <div>
          <span className="badge red">California Restricted Material</span>
          <p>
            A pesticide that California law restricts. Using it requires a permit from
            the county agricultural commissioner. This is a matter of law.
          </p>
        </div>
        <div>
          <span className="badge red">Federal Restricted Use</span>
          <p>
            Classified by the U.S. EPA as restricted use: it may only be applied by, or
            under the direct supervision of, a certified applicator. Also a matter of law.
          </p>
        </div>
        <div>
          <span className="badge red">{PUBLISHER} Watchlist</span>
          <p>
            A chemical {PUBLISHER} has chosen to highlight. This is an editorial
            judgement by {PUBLISHER}, <strong>not</strong> a legal restriction. A chemical
            can be watchlisted without being restricted, and restricted without being
            watchlisted.
          </p>
        </div>
        <div>
          <span className="badge orange">Environmental concern</span>
          <p>
            A sourced finding about the chemical&rsquo;s behaviour in the environment —
            groundwater listing, soil mobility, aquatic toxicity, runoff or persistence.
          </p>
        </div>
        <div>
          <span className="badge yellow">Label hazard</span>
          <p>
            Something the product&rsquo;s own registered label requires: a DANGER or
            WARNING signal word, protective equipment, or a buffer or aquatic-use
            restriction.
          </p>
        </div>
        <div>
          <span className="badge aerial">Aerial</span>
          <p>
            Applied from a helicopter or aircraft rather than from the ground. Shown
            because aerial application carries a different drift risk, not because it is
            a warning.
          </p>
        </div>
      </div>

      <p className="small muted" style={{ marginBottom: 0 }}>
        A red stripe down the left of a row means that application carries at least one
        red flag. An application with no flags is not thereby established as harmless —
        it means no flag has been recorded for the products reported on it.
      </p>
    </details>
  );
}
