import { notFound } from "next/navigation";
import Link from "next/link";
import { ApplicationGrid } from "@/components/ApplicationGrid";
import { JsonLd, breadcrumbs } from "@/components/JsonLd";
import { Legend } from "@/components/Legend";
import { api } from "@/lib/api";

interface Props {
  params: Promise<{ county: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}

export async function generateMetadata({ params }: Props) {
  const { county } = await params;
  const { counties } = await api.counties().catch(() => ({ counties: [] }));
  const match = counties.find((c) => c.slug === county);
  if (!match) return { title: "County not found" };
  return {
    title: `Herbicide applications in ${match.name} County, California`,
    description:
      `Forestry herbicide applications reported in ${match.name} County, California — ` +
      `${match.applications} applications covering ${Math.round(match.acres).toLocaleString()} reported acres.`,
    alternates: { canonical: `/applications/${match.slug}` },
  };
}

export default async function CountyPage({ params, searchParams }: Props) {
  const { county } = await params;
  const query = await searchParams;

  const { counties } = await api.counties().catch(() => ({ counties: [] }));
  const match = counties.find((c) => c.slug === county);
  if (!match) notFound();

  const page = Number(query.page ?? 1);
  const data = await api
    .applications({ county, page, year: query.year, method: query.method, chemical: query.chemical })
    .catch(() => ({ applications: [], total: 0, page: 1, pages: 0, page_size: 25 }));

  return (
    <>
      <JsonLd data={breadcrumbs([["Applications", "/applications"], [`${match.name} County`, `/applications/${match.slug}`]])} />
      <h1>Herbicide applications in {match.name} County</h1>
      <p className="lede">
        {match.applications} published application{match.applications === 1 ? "" : "s"},{" "}
        {Math.round(match.acres).toLocaleString()} acres reported treated
        {match.first_date && match.last_date &&
          `, ${match.first_date.slice(0, 4)}–${match.last_date.slice(0, 4)}`}
        .
      </p>

      <p>
        <Link href={`/map?county=${county}`}>View these applications on the map →</Link>
      </p>

      <Legend compact />
      <ApplicationGrid rows={data.applications} />

      {data.pages > 1 && (
        <div className="pager">
          {page > 1 && <Link href={`?page=${page - 1}`}>← Previous</Link>}
          <span className="muted small">Page {data.page} of {data.pages}</span>
          {page < data.pages && <Link href={`?page=${page + 1}`}>Next →</Link>}
        </div>
      )}
    </>
  );
}
