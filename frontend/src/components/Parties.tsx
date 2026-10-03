import Link from "next/link";
import { formatAmount, type OwnerTally, type PartyCompany, type Parties as PartiesData } from "@/lib/api";

/**
 * Who is behind an application: the property owner, the permit holder and
 * the people and businesses named on the permit in force on its dates.
 * Every line is from a county document; contact details the documents do
 * not carry are left out rather than guessed.
 */

function Contact({ c }: { c: PartyCompany }) {
  const bits = [
    c.website && (
      <a key="w" href={c.website.startsWith("http") ? c.website : `https://${c.website}`}
         rel="nofollow noopener">{c.website.replace(/^https?:\/\//, "")}</a>
    ),
    c.phone && <a key="p" href={`tel:${c.phone.replace(/[^\d+]/g, "")}`}>{c.phone}</a>,
    c.email && <a key="e" href={`mailto:${c.email}`}>{c.email}</a>,
  ].filter(Boolean);
  if (bits.length === 0) return null;
  return (
    <div className="party-contact">
      {bits.map((b, i) => (<span key={i}>{i > 0 && " · "}{b}</span>))}
    </div>
  );
}

/** Herbicide product applied on this landowner's ground, year by year. */
function OwnerTallyBlock({ tally }: { tally: OwnerTally }) {
  const amount = (c: { gallons: number; pounds: number }) =>
    [formatAmount(c.gallons, "gal"), formatAmount(c.pounds, "lb")].filter(Boolean).join(" · ") || "—";
  return (
    <div className="owner-tally">
      <div className="owner-tally-head">Herbicide on their land, by year</div>
      <table>
        <tbody>
          {tally.years.map((y) => (
            <tr key={y.year}>
              <td>{y.year}</td>
              <td>{amount(y)}</td>
              <td className="muted">{(y.acres ?? 0).toLocaleString("en-US", { maximumFractionDigits: 0 })} ac</td>
            </tr>
          ))}
        </tbody>
      </table>
      {tally.top_chemicals.length > 0 && (
        <div className="muted tiny" style={{ marginTop: 4 }}>Mostly {tally.top_chemicals.join(", ")}</div>
      )}
      <Link href={`/dashboard?owner=${encodeURIComponent(tally.key)}`} className="tiny">
        Full breakdown by chemical →
      </Link>
    </div>
  );
}

export function Parties({ parties, ownerTally }: { parties: PartiesData | undefined; ownerTally?: OwnerTally | null }) {
  if (!parties) return null;
  const { owner, operator, people, qualified_applicators: qals, contractors } = parties;
  const sameAsOwner = owner && operator &&
    owner.name.replace(/\W/g, "").toUpperCase() === operator.name.replace(/\W/g, "").toUpperCase();

  return (
    <section className="parties">
      <div className="party-grid">
        {owner && (
          <div className="party-card">
            <div className="party-role">Property owner</div>
            <div className="party-name">{owner.name}</div>
            {owner.address && <div className="muted small">{owner.address}</div>}
            <Contact c={owner} />
            {sameAsOwner && <div className="muted small">Also the permit holder.</div>}
            {ownerTally && <OwnerTallyBlock tally={ownerTally} />}
          </div>
        )}
        {operator && !sameAsOwner && (
          <div className="party-card">
            <div className="party-role">Permit holder / operator</div>
            <div className="party-name">{operator.name}</div>
            <Contact c={operator} />
          </div>
        )}
        {operator && (
          <div className="party-card">
            <div className="party-role">Restricted materials permit</div>
            <div className="party-name">{operator.permit_number}</div>
            {operator.operator_id && <div className="muted small">Operator ID {operator.operator_id}</div>}
            {sameAsOwner && <Contact c={operator} />}
          </div>
        )}
        {people.map((p) => (
          <div key={p.name} className="party-card person">
            {p.photo && (
              <figure className="party-photo">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={p.photo} alt={p.name} width={88} height={88} loading="lazy" />
              </figure>
            )}
            <div>
              <div className="party-role">{p.roles.join(" · ")}</div>
              <div className="party-name">{p.name}</div>
              {p.title && <div className="muted small">{p.title}</div>}
              <div className="muted small">Permit {p.permits.join(", ")}</div>
              {p.photo && p.photo_source && (
                <div className="muted tiny">Photo: {p.photo_source}</div>
              )}
            </div>
          </div>
        ))}
      </div>

      {(qals.length > 0 || contractors.length > 0) && (
        <table className="records" style={{ marginTop: 14 }}>
          <thead>
            <tr><th>Licensed on the permit</th><th>Licence</th><th>Type</th><th>Expires</th><th>Website / phone</th></tr>
          </thead>
          <tbody>
            {qals.map((q) => (
              <tr key={`q${q.license}`}>
                <td>Qualified applicator (held under {q.held_under})</td>
                <td><strong>QAL {q.license}</strong></td>
                <td className="small">{q.type}</td>
                <td className="small">{q.expires ?? "—"}</td>
                <td className="small">—</td>
              </tr>
            ))}
            {contractors.map((c) => (
              <tr key={`c${c.license}`}>
                <td>{c.name}</td>
                <td>{c.license}</td>
                <td className="small">Pest control business</td>
                <td className="small">{c.expires ?? "—"}</td>
                <td className="small">
                  {c.website && (
                    <a href={c.website.startsWith("http") ? c.website : `https://${c.website}`}
                       rel="nofollow noopener">{c.website.replace(/^https?:\/\//, "")}</a>
                  )}
                  {c.website && c.phone && <br />}
                  {c.phone ?? (c.website ? null : "—")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted tiny" style={{ marginTop: 8 }}>{parties.source_note}</p>
    </section>
  );
}
