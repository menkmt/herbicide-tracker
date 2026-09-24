import Link from "next/link";
import { ApplicationGrid } from "@/components/ApplicationGrid";
import { Legend } from "@/components/Legend";
import { api } from "@/lib/api";

export const metadata = {
  title: "All forestry herbicide applications in California",
  description: "Every published forestry herbicide application, filterable by county, chemical, company and method.",
};

interface Props {
  searchParams: Promise<Record<string, string | undefined>>;
}

/** Filters live in the URL so a filtered view can be linked, shared and cited. */
export default async function AllApplicationsPage({ searchParams }: Props) {
  const params = await searchParams;
  const page = Number(params.page ?? 1);

  const [data, counties, chemicals] = await Promise.all([
    api
      .applications({
        page,
        county: params.county,
        year: params.year,
        chemical: params.chemical,
        applicator: params.applicator,
        landowner: params.landowner,
        method: params.method,
        flagged: params.flagged,
        kind: params.kind,
        q: params.q,
      })
      .catch(() => ({ applications: [], total: 0, page: 1, pages: 0, page_size: 25 })),
    api.counties().catch(() => ({ counties: [] })),
    api.chemicals().catch(() => ({ chemicals: [] })),
  ]);

  const query = (overrides: Record<string, string | number | undefined>) => {
    const next = new URLSearchParams();
    for (const [key, value] of Object.entries({ ...params, ...overrides })) {
      if (value !== undefined && value !== "") next.set(key, String(value));
    }
    return `?${next.toString()}`;
  };

  return (
    <>
      <h1>Applications</h1>
      <p className="lede">
        {data.total.toLocaleString()} published application
        {data.total === 1 ? "" : "s"}. Each row groups every pesticide use report
        filed for one project.
      </p>

      <form className="filters panel" method="get">
        <div className="field">
          <label htmlFor="q">Search</label>
          <input id="q" name="q" defaultValue={params.q ?? ""} placeholder="Owner, applicator, site ID, permit" />
        </div>
        <div className="field">
          <label htmlFor="county">County</label>
          <select id="county" name="county" defaultValue={params.county ?? ""}>
            <option value="">All counties</option>
            {counties.counties.map((county) => (
              <option key={county.slug} value={county.slug}>{county.name}</option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="chemical">Chemical</label>
          <select id="chemical" name="chemical" defaultValue={params.chemical ?? ""}>
            <option value="">Any chemical</option>
            {chemicals.chemicals.map((chemical) => (
              <option key={chemical.slug} value={chemical.name}>{chemical.name}</option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="method">Method</label>
          <select id="method" name="method" defaultValue={params.method ?? ""}>
            <option value="">Aerial and ground</option>
            <option value="aerial">Aerial only</option>
            <option value="ground">Ground only</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="year">Year</label>
          <input id="year" name="year" type="number" min="2020" max="2100" defaultValue={params.year ?? ""} />
        </div>
        <div className="field">
          <label htmlFor="flagged">Flagged</label>
          <select id="flagged" name="flagged" defaultValue={params.flagged ?? ""}>
            <option value="">All applications</option>
            <option value="true">Restricted or watchlisted only</option>
          </select>
        </div>
        <button type="submit" className="primary">Apply filters</button>
        <Link href="/applications" className="small">Clear</Link>
      </form>

      <Legend compact />
      <ApplicationGrid rows={data.applications} />

      {data.pages > 1 && (
        <div className="pager">
          {page > 1 && <Link href={query({ page: page - 1 })}>← Previous</Link>}
          <span className="muted small">Page {data.page} of {data.pages}</span>
          {page < data.pages && <Link href={query({ page: page + 1 })}>Next →</Link>}
        </div>
      )}
    </>
  );
}
