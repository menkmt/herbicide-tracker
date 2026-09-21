import { ParcelMap } from "@/components/ParcelMap";
import { api } from "@/lib/api";

export const metadata = {
  title: "Map",
  description: "Interactive map of forestry herbicide applications and the parcels associated with them.",
};

interface Props {
  searchParams: Promise<Record<string, string | undefined>>;
}

export default async function MapPage({ searchParams }: Props) {
  const params = await searchParams;
  const { counties } = await api.counties().catch(() => ({ counties: [] }));

  const query = new URLSearchParams();
  if (params.county) query.set("county", params.county);
  if (params.year) query.set("year", params.year);

  return (
    <>
      <h1>Application map</h1>
      <p className="lede">
        Parcels associated with published applications. Click an outline for a summary
        and a link to the full record. Flagged applications are outlined in red.
      </p>

      <form className="filters" method="get">
        <div className="field">
          <label htmlFor="county">County</label>
          <select id="county" name="county" defaultValue={params.county ?? ""}>
            <option value="">All counties</option>
            {counties.map((county) => (
              <option key={county.slug} value={county.slug}>{county.name}</option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="year">Year</label>
          <input id="year" name="year" type="number" min="2020" defaultValue={params.year ?? ""} />
        </div>
        <button type="submit" className="primary">Update map</button>
      </form>

      <ParcelMap source={`/api/map/applications?${query.toString()}`} tall />

      <p className="small muted" style={{ marginTop: 10 }}>
        Outlines are the properties associated with each application, not measurements
        of the area treated. An application is shown only once its parcels have been
        matched and an administrator has published it.
      </p>
    </>
  );
}
