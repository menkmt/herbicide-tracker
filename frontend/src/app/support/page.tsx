import Link from "next/link";

export const metadata = {
  title: "Support this work",
  description:
    "Herbicide Tracker California is free to use and costs money to run. A one-time " +
    "donation keeps the records public.",
};

/**
 * One-time donations only — no memberships, no tiers, no recurring charge.
 *
 * The button is whatever payment link the operator configured; the site
 * itself never handles money or learns who gave. With no link configured the
 * page still explains what the money is for, so the ask is never a dead end.
 */
const DONATE_URL = process.env.TRACKER_DONATE_URL?.trim() || null;
const DONATE_LABEL = process.env.TRACKER_DONATE_LABEL?.trim() || "Make a one-time donation";

export default function SupportPage() {
  return (
    <>
      <span className="eyebrow">Independent · Free to use · Public records</span>
      <h1>
        This costs money to run.{" "}
        <span className="gradient-text">It stays free anyway.</span>
      </h1>
      <p className="lede">
        Every page on this site is built from public records — pesticide use reports,
        notices of intent and permits that companies are required to file with county
        agricultural commissioners. Getting those records, reading them, checking them
        and putting them on a map is work, and hosting them is a bill. Nobody pays us to
        do it. If it is useful to you, a one-time donation keeps it going.
      </p>

      <div className="panel donate">
        {DONATE_URL ? (
          <a className="donate-btn" href={DONATE_URL} rel="noopener">
            {DONATE_LABEL}
          </a>
        ) : (
          <p className="muted" style={{ margin: 0 }}>
            The donation link is being set up. Check back shortly.
          </p>
        )}
        <p className="small muted" style={{ margin: "12px 0 0" }}>
          One-time only. No account, no subscription, nothing recurring. Payment is handled
          by the donation platform; this site never sees your card details and does not
          record who gave.
        </p>
      </div>

      <h2>What it pays for</h2>
      <div className="cards">
        <div className="card">
          <div className="n" style={{ fontSize: "1.3rem" }}>Public records requests</div>
          <div className="k" style={{ textTransform: "none", letterSpacing: 0, fontSize: ".9rem" }}>
            Each county is asked, under the California Public Records Act, for its
            reports every month. Some charge copying fees; all of it takes follow-up.
          </div>
        </div>
        <div className="card">
          <div className="n" style={{ fontSize: "1.3rem" }}>Reading the paperwork</div>
          <div className="k" style={{ textTransform: "none", letterSpacing: 0, fontSize: ".9rem" }}>
            Records arrive as scans, spreadsheets and Word files in a different layout
            from every county. Software reads them; a person checks the exceptions.
          </div>
        </div>
        <div className="card">
          <div className="n" style={{ fontSize: "1.3rem" }}>Servers, maps and the domain</div>
          <div className="k" style={{ textTransform: "none", letterSpacing: 0, fontSize: ".9rem" }}>
            The site, the database, the map tiles and nightly backups run on a rented
            server that costs about thirty dollars a month before anything else.
          </div>
        </div>
        <div className="card">
          <div className="n" style={{ fontSize: "1.3rem" }}>Building what is missing</div>
          <div className="k" style={{ textTransform: "none", letterSpacing: 0, fontSize: ".9rem" }}>
            More counties, roadside and waterway treatments, enforcement histories on
            company pages. Every one of those is time.
          </div>
        </div>
      </div>

      <h2>What it does not pay for</h2>
      <p>
        Access. The records on this site are public and will stay public. There is no
        paywall, no members-only tier and no version of this site where the data is
        hidden behind a donation. The point is that people can see what is being sprayed
        near them, and that does not work if only some people can see it.
      </p>

      <h2>Other ways to help</h2>
      <ul>
        <li>
          <strong>Send us records.</strong> If you have obtained pesticide use reports,
          notices of intent or permits from a county yourself, they can be added.
        </li>
        <li>
          <strong>Tell us what is wrong.</strong> Every application page lists the source
          documents it was built from. If something does not match, say so.
        </li>
        <li>
          <strong>Share it.</strong> A link to a specific application or the{" "}
          <Link href="/near-me">search near an address</Link> page is the fastest way to
          put this in front of someone it affects.
        </li>
      </ul>
    </>
  );
}
