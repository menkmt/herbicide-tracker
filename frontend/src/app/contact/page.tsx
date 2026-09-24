import Link from "next/link";

export const metadata = {
  title: "Contact",
  description:
    "Send records, report an error, or ask about the data behind Herbicide Tracker California.",
};

// The address comes from the server's environment so the page is never
// prerendered with an empty one.
export const dynamic = "force-dynamic";

const EMAIL = process.env.TRACKER_CONTACT_EMAIL?.trim() || null;

export default function ContactPage() {
  return (
    <>
      <h1>Contact</h1>
      <p className="lede">
        One address for everything. Say which of the things below it is about and it
        gets to the right place.
      </p>

      <div className="panel donate">
        {EMAIL ? (
          <a className="donate-btn" href={`mailto:${EMAIL}`}>{EMAIL}</a>
        ) : (
          <p className="muted" style={{ margin: 0 }}>
            The contact address is being set up. Check back shortly.
          </p>
        )}
      </div>

      <h2>Report an error</h2>
      <p>
        Every application page lists the source documents it was built from. If a
        figure, a name, a location or a grouping looks wrong, send the address of the
        page and what you think it should say. Corrections are checked against the
        source record and the page is updated, with the change noted.
      </p>

      <h2>Send records</h2>
      <p>
        Pesticide use reports, notices of intent, restricted materials permits,
        notices of proposed action or investigation reports you have obtained from a
        county or from the Department of Pesticide Regulation can be added to the
        tracker. Scans, spreadsheets and Word files are all fine. Say which county and
        which year they cover.
      </p>

      <h2>You are named on this site</h2>
      <p>
        The records here are public documents filed with county agricultural
        commissioners, published as received. If you are an operator, applicator or
        landowner and believe a record is misattributed or a grouping is wrong, write
        with the permit or site ID and the correction. Requests to remove an accurate
        public record are declined.
      </p>

      <h2>Press and researchers</h2>
      <p>
        Totals by county, chemical, company or year can be provided from the same data
        the site shows, with the underlying records. Ask. See{" "}
        <Link href="/about">how the data is collected and checked</Link> first.
      </p>

      <h2>Support</h2>
      <p>
        The tracker runs on one-time donations. <Link href="/support">Here is why and what it pays for.</Link>
      </p>
    </>
  );
}
