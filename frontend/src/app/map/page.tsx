import { Legend } from "@/components/Legend";
import { ParcelMap } from "@/components/ParcelMap";
import { CALIFORNIA_COUNTIES, countySlug } from "@/lib/counties";

export const metadata = {
  title: "Map of forestry herbicide applications in California",
  description: "Interactive map of forestry herbicide applications and the parcels associated with them.",
};

interface Props {
  searchParams: Promise<Record<string, string | undefined>>;
}

export default async function MapPage({ searchParams }: Props) {
  const params = await searchParams;

  const query = new URLSearchParams();
  if (params.county) query.set("county", params.county);
  if (params.year) query.set("year", params.year);

  return (
    <>
      <h1>Application map</h1>
      <p className="lede">
        Every published application. Solid outlines are identified properties; dashed
        squares are the one-square-mile section a use report names, shown until the
        property is identified. Red means a restricted or watch-listed chemical. Click
        any outline for a summary and a link to the full record.
      </p>

      <form className="filters" method="get">
        <div className="field">
          <label htmlFor="county">County</label>
          <select id="county" name="county" defaultValue={params.county ?? ""}>
            <option value="">All counties</option>
            {CALIFORNIA_COUNTIES.map((name) => (
              <option key={name} value={countySlug(name)}>{name}</option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="year">Year</label>
          <input id="year" name="year" type="number" min="2020" defaultValue={params.year ?? ""} />
        </div>
        <button type="submit" className="primary">Update map</button>
      </form>

      <Legend compact />

      <ParcelMap source={`/api/map/applications?${query.toString()}`} tall />

      <p className="small muted" style={{ marginTop: 10 }}>
        Outlines are properties or reported sections, not measurements of the area
        treated. Use the Layers button for satellite, streets or topographic maps,
        streams and land ownership.
      </p>
    </>
  );
}
