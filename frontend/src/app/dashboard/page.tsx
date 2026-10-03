import type { Metadata } from "next";
import Link from "next/link";
import { YearChart } from "@/components/YearChart";
import { colorFor } from "@/lib/chemColors";
import { api, formatAmount, type TallyCell, type Tallies } from "@/lib/api";
import { CALIFORNIA_COUNTIES, countySlug } from "@/lib/counties";

interface Props {
  searchParams: Promise<Record<string, string | undefined>>;
}

const EMPTY: Tallies = {
  scope: { county: null, owner: null },
  years: [],
  totals: { by_year: {}, all: { applications: 0, gallons: 0, pounds: 0, acres: 0, unresolved: [] } },
  adjuvants: { by_year: {}, all: { applications: 0, gallons: 0, pounds: 0, unresolved: [] } },
  chemicals: [],
  counties: [],
  landowners: [],
  landowner_count: 0,
  held_out: [],
};

function scopeName(data: Tallies): string {
  if (data.scope.owner) return data.scope.owner.name;
  if (data.scope.county?.name) return `${data.scope.county.name} County`;
  return "California";
}

export async function generateMetadata({ searchParams }: Props): Promise<Metadata> {
  const params = await searchParams;
  const county = params.county
    ? CALIFORNIA_COUNTIES.find((name) => countySlug(name) === params.county)
    : undefined;
  const where = county ? `${county} County` : "California";
  return {
    title: `Forestry herbicide totals by year — ${where}`,
    description:
      `How much herbicide was applied to forest land in ${where} each year, by chemical, ` +
      "county and landowner, totalled from the counties' own pesticide use reports.",
    alternates: { canonical: county ? `/dashboard?county=${params.county}` : "/dashboard" },
    robots: params.owner ? { index: false } : undefined,
  };
}

/** Gallons over pounds, each only when present; never added together. */
function Amount({ cell }: { cell: TallyCell | undefined }) {
  if (!cell || (!cell.gallons && !cell.pounds && cell.unresolved.length === 0)) {
    return <span className="muted">—</span>;
  }
  return (
    <>
      {cell.gallons > 0 && <div>{formatAmount(cell.gallons, "gal")}</div>}
      {cell.pounds > 0 && <div>{formatAmount(cell.pounds, "lb")}</div>}
      {cell.unresolved.map((u) => (
        <div key={u.unit} className="muted tiny">+ {u.amount.toLocaleString()} {u.unit}</div>
      ))}
    </>
  );
}

function YearTable<T extends { by_year: Record<string, TallyCell>; all: TallyCell }>({
  rows,
  years,
  head,
  name,
  acres = true,
}: {
  rows: T[];
  years: number[];
  head: string;
  name: (row: T) => React.ReactNode;
  acres?: boolean;
}) {
  return (
    <div className="table-scroll">
      <table className="records tally-table">
        <thead>
          <tr>
            <th>{head}</th>
            {years.map((y) => <th key={y} className="num">{y}</th>)}
            <th className="num">All years</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              <td>{name(row)}</td>
              {years.map((y) => (
                <td key={y} className="num"><Amount cell={row.by_year[String(y)]} /></td>
              ))}
              <td className="num total">
                <Amount cell={row.all} />
                <div className="muted tiny">
                  {row.all.applications} application{row.all.applications === 1 ? "" : "s"}
                  {acres && row.all.acres ? ` · ${row.all.acres.toLocaleString("en-US", { maximumFractionDigits: 0 })} ac` : ""}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default async function DashboardPage({ searchParams }: Props) {
  const params = await searchParams;
  const data = await api.tallies({ county: params.county, owner: params.owner }).catch(() => EMPTY);
  const { years, totals } = data;
  const where = scopeName(data);
  const link = (overrides: Record<string, string | undefined>) => {
    const next = new URLSearchParams();
    for (const [key, value] of Object.entries({ county: params.county, owner: params.owner, ...overrides })) {
      if (value) next.set(key, value);
    }
    const q = next.toString();
    return q ? `/dashboard?${q}` : "/dashboard";
  };
  const series = (unit: "gallons" | "pounds") =>
    data.chemicals.map((c) => ({
      label: c.label,
      values: Object.fromEntries(Object.entries(c.by_year).map(([y, cell]) => [y, cell[unit]])),
    }));
  const range = years.length ? (years.length > 1 ? `${years[0]}–${years[years.length - 1]}` : `${years[0]}`) : "";

  return (
    <>
      <span className="eyebrow">
        {data.scope.owner ? "Landowner" : data.scope.county ? "County" : "Statewide"} · Totals by year
      </span>
      <h1>
        Herbicides on forest land: <span className="gradient-text">{where}</span>
      </h1>
      <p className="lede">
        Every gallon and pound of herbicide reported on forestry use reports, year by year,
        totalled from the county agricultural commissioners&rsquo; own records. Amounts are of
        the product as sold, not of active ingredient, and gallons and pounds are kept apart
        because they cannot honestly be added.
      </p>

      <form className="filters panel" method="get" action="/dashboard">
        <div className="field">
          <label htmlFor="county">County</label>
          <select id="county" name="county" defaultValue={params.county ?? ""}>
            <option value="">All of California</option>
            {CALIFORNIA_COUNTIES.map((name) => (
              <option key={name} value={countySlug(name)}>{name}</option>
            ))}
          </select>
        </div>
        {params.owner && <input type="hidden" name="owner" value={params.owner} />}
        <button type="submit" className="primary">Show</button>
        {data.scope.owner && (
          <Link href={link({ owner: undefined })} className="chip">
            Landowner: {data.scope.owner.name} ✕
          </Link>
        )}
        {(params.county || params.owner) && <Link href="/dashboard" className="small">Reset to statewide</Link>}
      </form>

      {years.length === 0 ? (
        <p className="muted">No published use reports for {where} yet.</p>
      ) : (
        <>
          <div className="cards stats">
            <div className="card"><div className="n">{formatAmount(totals.all.gallons, "gal") || "0 gal"}</div><div className="k">Liquid herbicide product, {range}</div></div>
            <div className="card"><div className="n">{formatAmount(totals.all.pounds, "lb") || "0 lb"}</div><div className="k">Dry herbicide product, {range}</div></div>
            <div className="card"><div className="n">{(totals.all.acres ?? 0).toLocaleString("en-US", { maximumFractionDigits: 0 })}</div><div className="k">Acres treated</div></div>
            <div className="card"><div className="n">{totals.all.applications.toLocaleString()}</div><div className="k">Applications</div></div>
          </div>

          <div className="chart-pair">
            <YearChart years={years} series={series("gallons")} unit="gal" title="Gallons of liquid herbicide product" />
            <YearChart years={years} series={series("pounds")} unit="lb" title="Pounds of dry herbicide product" />
          </div>

          <h2>By year</h2>
          <YearTable rows={[{ ...totals, name: where }]} years={years} head="Total" name={(r) => <strong>{r.name}</strong>} />
          {data.adjuvants.all.applications > 0 && (
            <p className="small muted" style={{ marginTop: 8 }}>
              Not counted above: {[formatAmount(data.adjuvants.all.gallons, "gal"), formatAmount(data.adjuvants.all.pounds, "lb")].filter(Boolean).join(" and ")}{" "}
              of tank additives (surfactants, oils, dyes) mixed in with the herbicides.
            </p>
          )}

          <h2>By chemical</h2>
          <YearTable
            rows={data.chemicals}
            years={years}
            head="Active ingredient(s)"
            acres={false}
            name={(c) => (
              <span className="chem-name">
                <span className="swatch" style={{ background: colorFor(c.label) }} />
                {c.ingredients.length ? c.ingredients.map((i, k) => (
                  <span key={i.slug}>{k > 0 && " + "}<Link href={i.url}>{i.name}</Link></span>
                )) : c.label}
              </span>
            )}
          />

          {!data.scope.county && !data.scope.owner && data.counties.length > 0 && (
            <>
              <h2>By county</h2>
              <YearTable rows={data.counties} years={years} head="County"
                         name={(c) => <Link href={link({ county: c.slug })}>{c.name}</Link>} />
            </>
          )}

          {!data.scope.owner && data.landowners.length > 0 && (
            <>
              <h2>By landowner</h2>
              <YearTable rows={data.landowners} years={years} head="Landowner"
                         name={(o) => <Link href={link({ owner: o.key })}>{o.name}</Link>} />
              {data.landowner_count > data.landowners.length && (
                <p className="small muted">Largest {data.landowners.length} of {data.landowner_count} landowners shown.</p>
              )}
            </>
          )}

          {data.held_out.length > 0 && (
            <>
              <h2>Records that don&rsquo;t add up</h2>
              <p className="small muted">
                These lines report a rate per acre more than ten times what the same product is
                normally reported at — almost always a dropped decimal point in the county&rsquo;s
                data. They are left out of the totals above rather than corrected, and shown here
                so the county can be asked to fix them.
              </p>
              <div className="table-scroll">
                <table className="records">
                  <thead>
                    <tr><th>Use report</th><th>Date</th><th>County</th><th>Product</th><th className="num">Reported</th><th className="num">Per acre</th><th className="num">Usual per acre</th></tr>
                  </thead>
                  <tbody>
                    {data.held_out.map((h, i) => (
                      <tr key={i}>
                        <td>{h.url ? <Link href={h.url}>{h.document_number ?? "View"}</Link> : h.document_number}</td>
                        <td>{h.date}</td>
                        <td>{h.county}</td>
                        <td>{h.product}</td>
                        <td className="num">{h.amount.toLocaleString()} {h.unit} on {h.acres.toLocaleString()} ac</td>
                        <td className="num">{h.rate.toLocaleString()}</td>
                        <td className="num">{h.typical_rate.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          <h2>How these totals are made</h2>
          <ul className="small muted method-list">
            <li>Only published forestry applications, and only what use reports say was applied. Notices of intent are plans, not use, and are left out.</li>
            <li>The same record sent in several formats is counted once.</li>
            <li>A product with two active ingredients is counted once, under the mix, so the chemicals add up to the yearly total.</li>
            <li>Rodent baits are not herbicides and are not counted. Tank additives are totalled separately.</li>
            <li>Ounces are read as fluid or weight ounces from the product&rsquo;s formulation; anything that still can&rsquo;t be converted is shown as &ldquo;+ amount unit&rdquo; rather than dropped.</li>
            <li>Totals grow as more counties answer records requests, so a low number can mean a county has not sent its records yet.</li>
          </ul>
        </>
      )}
    </>
  );
}
