import Link from "next/link";
import { api } from "@/lib/api";
import { CALIFORNIA_COUNTIES, TIMBER_COUNTIES, countySlug } from "@/lib/counties";

export const metadata = {
  title: "Herbicide applications by county — all 58 California counties",
  description:
    "Forestry herbicide and pesticide applications in every California county, from " +
    "county pesticide use reports. See which counties have published records and which " +
    "are still being obtained.",
  alternates: { canonical: "/counties" },
};

export default async function CountiesPage() {
  const { counties } = await api.counties().catch(() => ({ counties: [] }));
  const published = new Map(counties.map((c) => [c.slug, c]));

  const ordered = [...CALIFORNIA_COUNTIES].sort((a, b) => {
    const pa = published.has(countySlug(a)) ? 0 : TIMBER_COUNTIES.has(a) ? 1 : 2;
    const pb = published.has(countySlug(b)) ? 0 : TIMBER_COUNTIES.has(b) ? 1 : 2;
    return pa - pb || a.localeCompare(b);
  });

  return (
    <>
      <span className="eyebrow">Statewide</span>
      <h1>Every California county</h1>
      <p className="lede">
        The tracker covers all 58 counties. Records are requested from each county
        agricultural commissioner and published as they are obtained and checked, so
        some counties have applications up now and others are still being gathered. A
        county with nothing published is a county whose records have not arrived yet —
        not a county where nothing was sprayed.
      </p>

      <div className="cards">
        {ordered.map((name) => {
          const slug = countySlug(name);
          const data = published.get(slug);
          return (
            <Link key={slug} href={`/applications/${slug}`} className="card">
              <div className="n" style={{ fontSize: "1.2rem" }}>{name}</div>
              <div className="k">
                {data
                  ? `${data.applications} application${data.applications === 1 ? "" : "s"} · ${Math.round(data.acres).toLocaleString()} acres`
                  : "Records being obtained"}
              </div>
            </Link>
          );
        })}
      </div>
    </>
  );
}
