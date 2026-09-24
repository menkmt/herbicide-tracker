import Link from "next/link";
import { notFound } from "next/navigation";
import { FlagList } from "@/components/Flags";
import { Legend } from "@/components/Legend";
import { ParcelMap } from "@/components/ParcelMap";
import { ApiError, api, formatAcres, formatDateRange } from "@/lib/api";

interface Props {
  params: Promise<{ slug: string }>;
}

export async function generateMetadata({ params }: Props) {
  const { slug } = await params;
  try {
    const application = await api.application(slug);
    const when = formatDateRange(application.date_start, application.date_end);
    return {
      title: `${application.title} — ${when}`,
      description:
        `${application.title}: ${formatAcres(application.acres)} acres treated ` +
        `${when} in ${application.county ?? "California"} by ` +
        `${application.method === "aerial" ? "aerial" : "ground"} application, ` +
        `using ${application.chemicals.slice(0, 3).join(", ")}.`,
      alternates: { canonical: `/application/${slug}/` },
      openGraph: { title: `${application.title} — ${when}`, type: "article" },
    };
  } catch {
    return { title: "Application not found" };
  }
}

export default async function ApplicationPage({ params }: Props) {
  const { slug } = await params;
  let application;
  try {
    application = await api.application(slug);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }

  const when = formatDateRange(application.date_start, application.date_end);
  const totalProducts = new Set(
    application.records.flatMap((r) => r.products.map((p) => p.name).filter(Boolean)),
  );

  return (
    <>
      <h1>{application.title}</h1>
      <p className="lede">
        {when} · {formatAcres(application.acres, application.acreage_is_partial)} acres
        reported treated · {application.method === "aerial" ? "Aerial" : "Ground"} application
        {application.county && ` · ${application.county} County`}
      </p>

      {application.is_planned && (
        <div className="notice warn">
          This is a <strong>notice of intent</strong>. It records that the operator told
          the county they intended to apply a restricted material. It is not a report
          that the application took place.
        </div>
      )}

      {application.title_basis && (
        <p className="small muted">Title source: {application.title_basis}.</p>
      )}

      <h2>Where</h2>
      {application.parcels.length > 0 ? (
        <>
          <ParcelMap source={`/api/map/application/${slug}`} />
          <p className="small muted" style={{ marginTop: 8 }}>
            The outlined parcels are recorded to the operator named on this application
            and lie within the sections it reports. The outline shows property
            boundaries, not the area actually sprayed — the reports describe{" "}
            {formatAcres(application.acres)} treated acres.
          </p>
          <table className="records">
            <thead>
              <tr><th>Parcel (APN)</th><th>Owner of record</th><th>Parcel acres</th><th>Matched because</th></tr>
            </thead>
            <tbody>
              {application.parcels.map((parcel) => (
                <tr key={parcel.apn}>
                  <td>{parcel.apn}</td>
                  <td>{parcel.owner ?? "—"}</td>
                  <td>{parcel.acreage?.toLocaleString() ?? "—"}</td>
                  <td className="small muted">{parcel.match_basis ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <div className="notice">
          No parcel has been matched to this application yet, so no map is shown. The
          reported location is the public-land-survey section
          {application.mtrs.length === 1 ? " " : "s "}
          <strong>{application.mtrs.join(", ")}</strong>. A section is one square mile
          and is where the application was reported, not the area treated.
        </div>
      )}

      <dl className="facts" style={{ marginTop: 16 }}>
        <dt>Township / range / section</dt>
        <dd>{application.mtrs.join(", ") || "Not reported"}</dd>
        <dt>PUR site IDs</dt>
        <dd>{application.site_ids.join(", ") || "Not reported"}</dd>
        <dt>Permit numbers</dt>
        <dd>{application.permit_numbers.join(", ") || "Not reported"}</dd>
        <dt>Property owner / operator</dt>
        <dd>{application.owner ?? "Not reported"}</dd>
      </dl>

      <h2>Chemical warnings</h2>
      <FlagList flags={application.flags} />
      <Legend compact />

      <h2>What was in the tank</h2>
      {application.materials.active_ingredients.length === 0 &&
      application.materials.products.length === 0 ? (
        <p className="muted small">No products were reported on these records.</p>
      ) : (
        <div className="materials">
          {application.materials.active_ingredients.map((ingredient) => (
            <div key={ingredient.slug} className="material">
              <div className="ai">
                <Link href={ingredient.url}>{ingredient.name}</Link>{" "}
                {ingredient.is_california_restricted && (
                  <span className="badge red">California Restricted Material</span>
                )}{" "}
                {ingredient.is_watchlisted && (
                  <span className="badge red">Watchlist</span>
                )}
              </div>
              <div className="prod">
                Applied as {ingredient.products.join(", ")}
              </div>
            </div>
          ))}

          {application.materials.products
            .filter((product) => product.active_ingredients.length === 0)
            .map((product) => (
              <div key={product.name} className="material">
                <div className="ai">{product.name}</div>
                <div className="unidentified">
                  {product.identified
                    ? "Active ingredients for this product have not been recorded yet."
                    : "This product has not yet been identified from its registration number, so its active ingredients are unknown."}
                </div>
              </div>
            ))}
        </div>
      )}

      {application.materials.adjuvants.length > 0 && (
        <>
          <h3>Other materials added to the tank</h3>
          <p className="small muted">
            These are not pesticides and are not counted as active ingredients, but they
            were applied to the same ground.
          </p>
          <div className="materials">
            {application.materials.adjuvants.map((adjuvant) => (
              <div key={adjuvant.name} className="material additive">
                <div className="ai">
                  {adjuvant.name}{" "}
                  <span className="badge plain">{adjuvant.type_label}</span>
                </div>
                <div className="what">{adjuvant.description}</div>
              </div>
            ))}
          </div>
        </>
      )}

      <h2>Reported use records</h2>
      <p className="small muted">
        {application.record_count} pesticide use report
        {application.record_count === 1 ? "" : "s"}, {totalProducts.size} product
        {totalProducts.size === 1 ? "" : "s"}. Quantities are exactly as reported to the
        county.
      </p>
      <table className="records">
        <thead>
          <tr>
            <th>Report</th><th>Location</th><th>Date</th><th>Applicator</th>
            <th>Products</th><th>Acres</th>
          </tr>
        </thead>
        <tbody>
          {application.records.map((record) => (
            <tr key={record.document_number ?? `${record.site_id}-${record.date_start}`}>
              <td className="small">{record.document_number ?? "—"}</td>
              <td className="small">
                {record.mtrs ?? "—"}
                {record.site_id && <div className="muted">site {record.site_id}</div>}
              </td>
              <td className="small">{record.date_start ?? "—"}</td>
              <td className="small">
                {record.applicator ?? "—"}
                {record.applicator_license && (
                  <div className="muted">licence {record.applicator_license}</div>
                )}
              </td>
              <td className="small">
                {record.products.map((product, index) => (
                  <div key={index} style={{ marginBottom: 6 }}>
                    {product.name}
                    {product.quantity !== null && ` — ${product.quantity} ${product.units ?? ""}`}
                    {product.active_ingredients.length > 0 && (
                      <div className="muted">
                        {product.active_ingredients.join(", ")}
                      </div>
                    )}
                    {product.is_adjuvant && product.adjuvant_label && (
                      <div className="muted">{product.adjuvant_label} — not a pesticide</div>
                    )}
                    {product.epa_reg_no && (
                      <div className="muted">EPA reg. {product.epa_reg_no}</div>
                    )}
                  </div>
                ))}
              </td>
              <td className="small">
                {record.treated_amount ?? "—"} {record.treated_units ?? ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Sources</h2>
      <p className="small muted">
        This application was assembled from {application.record_count} pesticide use
        report{application.record_count === 1 ? "" : "s"} filed with the county
        agricultural commissioner
        {application.permit_numbers.length > 0 &&
          ` under permit ${application.permit_numbers.join(", ")}`}
        . Confidence in the grouping: {application.confidence}.
      </p>
      <p>
        <Link href="/about">How this data is collected and checked →</Link>
      </p>
    </>
  );
}
