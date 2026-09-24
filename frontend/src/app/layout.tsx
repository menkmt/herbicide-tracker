import type { Metadata } from "next";
import Link from "next/link";
import { JsonLd, siteBase } from "@/components/JsonLd";
import "./globals.css";

// Every page reads the server's environment (site URL, verification tags,
// contact and donation links) at request time. Prerendering at build would
// bake in the build machine's values instead. API responses stay cached by
// their own revalidate windows, so this costs nothing on the data side.
export const dynamic = "force-dynamic";

const NAME = "Herbicide Tracker California";
const TAGLINE = "Forestry herbicide and pesticide applications by county, chemical and address";
const DESCRIPTION =
  "A public, searchable record of forestry herbicide and pesticide applications in " +
  "California — what was sprayed, where, by whom and how much — built from county " +
  "pesticide use reports, notices of intent and restricted materials permits.";

export function generateMetadata(): Metadata {
  const base = siteBase();
  return {
    // Makes every canonical and Open Graph URL absolute, which is what
    // search engines and link previews need.
    metadataBase: new URL(base),
    title: {
      default: `${NAME} — ${TAGLINE}`,
      template: `%s · ${NAME}`,
    },
    description: DESCRIPTION,
    applicationName: NAME,
    openGraph: {
      type: "website",
      siteName: NAME,
      title: `${NAME} — ${TAGLINE}`,
      description: DESCRIPTION,
      url: base,
      locale: "en_US",
    },
    twitter: {
      card: "summary_large_image",
      title: `${NAME} — ${TAGLINE}`,
      description: DESCRIPTION,
    },
    robots: { index: true, follow: true },
    // Search Console / Bing Webmaster ownership proofs, when configured.
    verification: {
      google: process.env.TRACKER_GOOGLE_SITE_VERIFICATION?.trim() || undefined,
      other: process.env.TRACKER_BING_SITE_VERIFICATION?.trim()
        ? { "msvalidate.01": process.env.TRACKER_BING_SITE_VERIFICATION.trim() }
        : undefined,
    },
  };
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const base = siteBase();
  return (
    <html lang="en">
      <body>
        <JsonLd
          data={{
            "@context": "https://schema.org",
            "@graph": [
              {
                "@type": "WebSite",
                "@id": `${base}/#website`,
                url: base,
                name: NAME,
                description: DESCRIPTION,
                inLanguage: "en-US",
                potentialAction: {
                  "@type": "SearchAction",
                  target: { "@type": "EntryPoint", urlTemplate: `${base}/near-me?address={address}` },
                  "query-input": "required name=address",
                },
              },
              {
                "@type": "Dataset",
                "@id": `${base}/#dataset`,
                name: "California forestry pesticide use reports, 2020 onward",
                description:
                  "Pesticide use reports, notices of intent and restricted materials permits " +
                  "for forestry and timberland applications, obtained from California county " +
                  "agricultural commissioners under the Public Records Act and grouped into " +
                  "applications.",
                url: `${base}/applications`,
                isAccessibleForFree: true,
                spatialCoverage: { "@type": "Place", name: "California, United States" },
                temporalCoverage: "2020-01-01/..",
                creator: { "@type": "Organization", name: NAME, url: base },
              },
            ],
          }}
        />
        <header className="site">
          <div className="wrap inner">
            <Link href="/" className="brand">
              <span className="mark">HT</span>
              <span>
                Herbicide Tracker <span className="state">California</span>
              </span>
            </Link>
            <nav>
              <Link href="/applications">Applications</Link>
              <Link href="/map">Map</Link>
              <Link href="/chemical">Chemicals</Link>
              <Link href="/about">About the data</Link>
              <Link href="/support" className="support">Support</Link>
              <Link href="/contact">Contact</Link>
              <Link href="/near-me" className="cta">
                Search near me
              </Link>
            </nav>
          </div>
        </header>
        <main className="wrap">{children}</main>
        <footer className="site">
          <div className="wrap cols">
            <div>
              <h4>{NAME}</h4>
              <p style={{ marginTop: 0 }}>
                Built from public records obtained from California county agricultural
                commissioners. Every figure on this site comes from a source document;
                each application page lists the records it was built from.
              </p>
              <p>
                Parcel outlines show property associated with an application. They are
                not a measurement of the area actually treated.
              </p>
            </div>
            <div>
              <h4>Explore</h4>
              <ul>
                <li><Link href="/applications">All applications</Link></li>
                <li><Link href="/map">Map</Link></li>
                <li><Link href="/chemical">Chemicals</Link></li>
                <li><Link href="/near-me">Search near an address</Link></li>
              </ul>
            </div>
            <div>
              <h4>About</h4>
              <ul>
                <li><Link href="/about">Where the data comes from</Link></li>
                <li><Link href="/about#warnings">What the colours mean</Link></li>
                <li><Link href="/support">Support this work</Link></li>
                <li><Link href="/contact">Contact</Link></li>
              </ul>
            </div>
          </div>
        </footer>
      </body>
    </html>
  );
}
