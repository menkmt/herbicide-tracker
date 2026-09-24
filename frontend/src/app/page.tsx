import Link from "next/link";
import { ApplicationGrid } from "@/components/ApplicationGrid";
import { api } from "@/lib/api";

export const metadata = {
  description:
    "Search forestry herbicide and pesticide applications in California by county, " +
    "chemical, company or address.",
};

export default async function HomePage() {
  const [counties, recent, meta] = await Promise.all([
    api.counties().catch(() => ({ counties: [] })),
    api.applications({ page_size: 10 }).catch(() => ({ applications: [], total: 0 })),
    api.meta().catch(() => null),
  ]);

  const totalAcres = counties.counties.reduce((sum, c) => sum + c.acres, 0);

  return (
    <>
      <section className="hero">
        <span className="eyebrow">Public record · California forest land</span>
        <h1>
          Every forestry herbicide application,{" "}
          <span className="gradient-text">on the record.</span>
        </h1>
        <p className="lede">
          What was sprayed, where, by whom and how much — assembled from the pesticide
          use reports, notices of intent and restricted materials permits that
          companies must file with county agricultural commissioners. Every
          application links to the documents it was built from.
        </p>

        {/* The address is used for the search only and is never stored. */}
        <form action="/near-me" method="get" className="search">
          <input
            id="address"
            name="address"
            aria-label="Address"
            placeholder="Enter an address to see what was sprayed nearby"
            required
          />
          <select id="miles" name="miles" defaultValue="1" aria-label="Radius">
            {[0.5, 1, 2, 5, 10, 25].map((value) => (
              <option key={value} value={value}>
                within {value} mile{value === 1 ? "" : "s"}
              </option>
            ))}
          </select>
          <button type="submit" className="primary">
            Search
          </button>
        </form>
      </section>

      <div className="cards stats">
        <div className="card">
          <div className="n">{recent.total.toLocaleString()}</div>
          <div className="k">Applications published</div>
        </div>
        <div className="card">
          <div className="n">{Math.round(totalAcres).toLocaleString()}</div>
          <div className="k">Acres reported treated</div>
        </div>
        <div className="card">
          <div className="n">{counties.counties.length}</div>
          <div className="k">Counties covered</div>
        </div>
        <div className="card">
          <div className="n">{meta?.coverage_start.slice(0, 4) ?? "2020"}</div>
          <div className="k">Records from</div>
        </div>
      </div>

      <h2>Browse by county</h2>
      {counties.counties.length === 0 ? (
        <p className="muted">No counties have published applications yet.</p>
      ) : (
        <div className="cards">
          {counties.counties.map((county) => (
            <Link key={county.slug} href={county.url} className="card">
              <div className="n" style={{ fontSize: "1.3rem" }}>{county.name}</div>
              <div className="k">
                {county.applications} application{county.applications === 1 ? "" : "s"} ·{" "}
                {Math.round(county.acres).toLocaleString()} acres
              </div>
            </Link>
          ))}
        </div>
      )}

      <h2>Most recent applications</h2>
      <ApplicationGrid rows={recent.applications} />
      <p style={{ marginTop: 16 }}>
        <Link href="/herbicide-tracker">See all applications →</Link>
      </p>
    </>
  );
}
