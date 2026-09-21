import { api } from "@/lib/api";

export const metadata = {
  title: "About the data",
  description: "Where this tracker's data comes from, what it covers, and what it deliberately does not claim.",
};

export default async function AboutPage() {
  const meta = await api.meta().catch(() => null);

  return (
    <>
      <h1>About the data</h1>

      <h2>Where it comes from</h2>
      <p>
        Anyone applying pesticides commercially in California must report it to the
        county agricultural commissioner. Applications of restricted materials also
        require a permit, and a notice of intent before each application. This tracker
        is built from those three record types, obtained from county agricultural
        commissioners under the California Public Records Act.
      </p>

      <h2>What it covers</h2>
      <ul>
        <li>Records from {meta?.coverage_start.slice(0, 4) ?? "2020"} onward.</li>
        <li>
          Forestry and timberland applications. Agricultural applications are outside
          this tracker&rsquo;s scope. Roadside, invasive-plant and waterway treatments
          are recognised and stored but not yet published.
        </li>
        <li>
          Counties whose records have been obtained and checked. An absence of
          applications for a county means nothing has been published for it, not that
          no applications occurred.
        </li>
      </ul>

      <h2>How an application is assembled</h2>
      <p>
        A single forestry project is usually reported as many separate use reports — one
        per square-mile section, sometimes one per day. This tracker groups reports that
        share an operator, a permit, a time and a location into one application, so the
        public sees the project rather than the paperwork. Every underlying report is
        kept and listed on the application&rsquo;s page.
      </p>

      <h2>What the maps do and do not show</h2>
      <p>
        A pesticide use report gives a location as a public-land-survey section — one
        square mile — together with the acreage treated. Where the operator&rsquo;s
        parcels within that section can be identified from county assessor records, the
        map outlines those parcels.
      </p>
      <p>
        <strong>
          An outline shows property associated with an application. It is not a
          measurement of the area actually sprayed.
        </strong>{" "}
        An operator who reported treating 63 acres inside a 640-acre property did not
        treat the whole property. The reported acreage is always shown alongside.
      </p>

      <h2>Chemical warnings</h2>
      <p>
        Two different things are both shown in red, and the tracker always says which
        it means:
      </p>
      <ul>
        <li>
          <strong>Regulatory restrictions</strong> — a California restricted material or
          a federally restricted-use pesticide. These are matters of law, cited to the
          document that establishes them, usually the county&rsquo;s own permit.
        </li>
        <li>
          <strong>Protect Lassen watchlist</strong> — chemicals Protect Lassen has
          chosen to highlight. This is an editorial judgement, not a legal status, and
          is labelled as such wherever it appears.
        </li>
      </ul>

      <h2>Accuracy and corrections</h2>
      <p>
        Every published figure comes from a source document, and each page lists the
        records behind it. Where a record is ambiguous — a site code that disagrees with
        its printed legal description, an owner name that matches no parcel, a product
        that cannot be identified — it is held back for a person to check rather than
        published with a guess.
      </p>
      {meta?.notes && (
        <ul className="small muted">
          {meta.notes.map((note) => (<li key={note}>{note}</li>))}
        </ul>
      )}
    </>
  );
}
