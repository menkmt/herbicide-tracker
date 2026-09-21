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
      <h1>Herbicide applications on California forest land</h1>
      <p className="lede">
        Built from the pesticide use reports, notices of intent and restricted
        materials permits that companies are required to file with county
        agricultural commissioners. Every application below links to the records it
        was built from.
      </p>

      <div className="cards">
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
              <div className="n">{county.name}</div>
              <div className="k">
                {county.applications} application{county.applications === 1 ? "" : "s"} ·{" "}
                {Math.round(county.acres).toLocaleString()} acres
              </div>
            </Link>
          ))}
        </div>
      )}

      <h2>Search near an address</h2>
      <div className="panel">
        <p className="small muted" style={{ marginTop: 0 }}>
          Enter an address to find applications near it. The address is used for the
          search only and is not stored.
        </p>
        <form action="/near-me" method="get" className="filters">
          <div className="field" style={{ flex: "1 1 320px" }}>
            <label htmlFor="address">Address</label>
            <input id="address" name="address" placeholder="e.g. 175 Russell Ave, Susanville CA" required />
          </div>
          <div className="field">
            <label htmlFor="miles">Radius</label>
            <select id="miles" name="miles" defaultValue="1">
              {[0.5, 1, 2, 5, 10, 25].map((value) => (
                <option key={value} value={value}>
                  {value} mile{value === 1 ? "" : "s"}
                </option>
              ))}
            </select>
          </div>
          <button type="submit" className="primary">
            Search applications
          </button>
        </form>
      </div>

      <h2>Most recent applications</h2>
      <ApplicationGrid rows={recent.applications} />
      <p style={{ marginTop: 14 }}>
        <Link href="/herbicide-tracker">See all applications →</Link>
      </p>
    </>
  );
}
