import Link from "next/link";
import { ParcelMap } from "@/components/ParcelMap";
import { api, formatAcres } from "@/lib/api";

export const metadata = {
  title: "Search near an address",
  description: "Find forestry herbicide applications within a chosen distance of an address.",
  robots: { index: true, follow: true },
};

interface Props {
  searchParams: Promise<Record<string, string | undefined>>;
}

const RADII = [0.5, 1, 2, 5, 10, 25];

export default async function NearMePage({ searchParams }: Props) {
  const params = await searchParams;
  const address = params.address;
  const miles = Number(params.miles ?? 1);

  let result = null;
  let error: string | null = null;
  if (address) {
    try {
      result = await api.radius({ address, miles });
    } catch {
      error =
        "That address could not be located, or the address lookup service is not " +
        "configured for this deployment.";
    }
  }

  return (
    <>
      <h1>Search near an address</h1>
      <p className="lede">
        Distance is measured to the nearest edge of an application&rsquo;s parcels, not
        to their centre — so a property next to a large treated parcel is correctly
        reported as close to it.
      </p>

      <form className="filters panel" method="get">
        <div className="field" style={{ flex: "1 1 340px" }}>
          <label htmlFor="address">Address</label>
          <input id="address" name="address" defaultValue={address ?? ""} required
                 placeholder="e.g. 175 Russell Ave, Susanville CA" />
        </div>
        <div className="field">
          <label htmlFor="miles">Radius</label>
          <select id="miles" name="miles" defaultValue={String(miles)}>
            {RADII.map((value) => (
              <option key={value} value={value}>{value} mile{value === 1 ? "" : "s"}</option>
            ))}
          </select>
        </div>
        <button type="submit" className="primary">Search</button>
      </form>

      <p className="small muted">
        The address you enter is used for this search only. It is not stored.
      </p>

      {error && <div className="notice warn">{error}</div>}

      {result && (
        <>
          <h2>
            {result.count} application{result.count === 1 ? "" : "s"} within{" "}
            {result.radius_miles} mile{result.radius_miles === 1 ? "" : "s"}
          </h2>
          {result.centre.resolved && (
            <p className="small muted">Searched from: {result.centre.resolved}</p>
          )}

          <ParcelMap
            source={`/api/map/applications`}
            radius={{ lat: result.centre.lat, lon: result.centre.lon, miles: result.radius_miles }}
          />

          {result.results.length === 0 ? (
            <p className="muted" style={{ marginTop: 16 }}>
              No published applications fall within this radius. That means none have
              been published here — not necessarily that none occurred.
            </p>
          ) : (
            <table className="records" style={{ marginTop: 16 }}>
              <thead>
                <tr><th>Distance</th><th>Application</th><th>Date</th><th>Acres</th><th>Method</th><th>Flags</th></tr>
              </thead>
              <tbody>
                {result.results.map((row) => (
                  <tr key={row.slug}>
                    <td>{row.distance_miles.toFixed(2)} mi</td>
                    <td><Link href={row.url}>{row.title}</Link></td>
                    <td>{row.date ?? "—"}</td>
                    <td>{formatAcres(row.acres)}</td>
                    <td>{row.method === "aerial" ? "Aerial" : "Ground"}</td>
                    <td>
                      {row.flag_headline && (
                        <span className={`badge ${row.flag_level}`}>
                          {row.flag_headline.replace(/^\w+\s*—\s*/, "")}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </>
  );
}
