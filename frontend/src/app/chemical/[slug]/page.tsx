import { notFound } from "next/navigation";
import { ApplicationGrid } from "@/components/ApplicationGrid";
import { ApiError, api } from "@/lib/api";

interface Props {
  params: Promise<{ slug: string }>;
}

export async function generateMetadata({ params }: Props) {
  const { slug } = await params;
  try {
    const chemical = await api.chemical(slug);
    return {
      title: chemical.name,
      description:
        `${chemical.name} in California forestry herbicide applications: regulatory ` +
        `status, environmental information and every tracked application that used it.`,
      alternates: { canonical: `/chemical/${slug}/` },
    };
  } catch {
    return { title: "Chemical not found" };
  }
}

const SECTION_TITLES: Record<string, string> = {
  overview: "Overview",
  groundwater: "Groundwater",
  surface_water: "Surface water and runoff",
  persistence: "Persistence",
  ecological: "Ecological information",
  human_health: "Human health",
};

export default async function ChemicalPage({ params }: Props) {
  const { slug } = await params;
  let chemical;
  try {
    chemical = await api.chemical(slug);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }

  const written = Object.entries(SECTION_TITLES).filter(
    ([key]) => chemical.sections[key],
  );

  return (
    <>
      <h1>{chemical.name}</h1>
      <p className="lede">
        {chemical.chemical_class ?? "Active ingredient"}
        {chemical.cas_number && ` · CAS ${chemical.cas_number}`}
      </p>

      <h2>Status</h2>
      {chemical.flags.length === 0 ? (
        <p className="muted small">No regulatory or watchlist flags are recorded.</p>
      ) : (
        chemical.flags.map((flag, index) => (
          <div key={index} className={`flag-block ${flag.level}`}>
            <strong>{flag.label}</strong>
            <div>{flag.detail}</div>
            <div className="src">
              {flag.is_regulatory
                ? "Regulatory status, from: "
                : "Protect Lassen editorial flag, from: "}
              {flag.source_url ? (
                <a href={flag.source_url} rel="nofollow noopener">{flag.source}</a>
              ) : (
                flag.source
              )}
            </div>
          </div>
        ))
      )}

      {written.length > 0 ? (
        written.map(([key, title]) => (
          <section key={key}>
            <h2>{title}</h2>
            <p>{chemical.sections[key]}</p>
          </section>
        ))
      ) : (
        <div className="notice">
          Sourced environmental and health information for {chemical.name} has not been
          added yet. Rather than summarise from memory, this section stays empty until
          it can cite the agency findings it is drawn from.
        </div>
      )}

      <h2>Products in this tracker</h2>
      {chemical.products.length === 0 ? (
        <p className="muted small">No products have been linked to this ingredient yet.</p>
      ) : (
        <table className="records">
          <thead><tr><th>Product</th><th>EPA registration</th><th>Registrant</th></tr></thead>
          <tbody>
            {chemical.products.map((product) => (
              <tr key={product.epa_reg_no}>
                <td>{product.name ?? "—"}</td>
                <td>{product.epa_reg_no}</td>
                <td>{product.registrant ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>Applications using {chemical.name}</h2>
      <ApplicationGrid rows={chemical.applications} />
    </>
  );
}
