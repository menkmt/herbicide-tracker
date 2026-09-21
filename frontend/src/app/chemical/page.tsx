import Link from "next/link";
import { api } from "@/lib/api";

export const metadata = {
  title: "Chemicals",
  description: "Active ingredients applied in the forestry herbicide applications this tracker covers.",
};

export default async function ChemicalIndexPage() {
  const { chemicals } = await api.chemicals().catch(() => ({ chemicals: [] }));

  return (
    <>
      <h1>Chemicals</h1>
      <p className="lede">
        Active ingredients identified in the products reported on these applications.
        Regulatory status and the editorial watchlist are shown separately,
        because they mean different things.
      </p>
      {chemicals.length === 0 ? (
        <p className="muted">No chemicals have been published yet.</p>
      ) : (
        <div className="cards">
          {chemicals.map((chemical) => (
            <Link key={chemical.slug} href={chemical.url} className="card">
              <div className="n" style={{ fontSize: "1.1rem" }}>{chemical.name}</div>
              <div className="k">{chemical.pesticide_type ?? "Active ingredient"}</div>
              <div style={{ marginTop: 8, display: "flex", gap: 6, flexWrap: "wrap" }}>
                {chemical.is_california_restricted && (
                  <span className="badge red">California Restricted Material</span>
                )}
                {chemical.is_watchlisted && (
                  <span className="badge red">Editorial Watchlist</span>
                )}
              </div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
