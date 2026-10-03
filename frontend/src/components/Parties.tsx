import type { PartyCompany, Parties as PartiesData } from "@/lib/api";

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

export function Parties({ parties }: { parties: PartiesData | undefined }) {
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
            {operator.phone_source && <div className="muted small">Phone from the {operator.phone_source}</div>}
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
            <tr><th>Licensed on the permit</th><th>Licence</th><th>Type</th><th>Expires</th><th>Phone</th></tr>
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
                <td className="small">{c.phone ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted tiny" style={{ marginTop: 8 }}>{parties.source_note}</p>
    </section>
  );
}
