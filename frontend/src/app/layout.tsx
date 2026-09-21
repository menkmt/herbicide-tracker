import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Protect Lassen Herbicide Tracker",
    template: "%s · Protect Lassen Herbicide Tracker",
  },
  description:
    "A public, searchable record of forestry herbicide and pesticide applications in " +
    "California, built from county pesticide use reports, notices of intent and " +
    "restricted materials permits.",
  openGraph: {
    type: "website",
    siteName: "Protect Lassen Herbicide Tracker",
  },
  robots: { index: true, follow: true },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="site">
          <div className="wrap inner">
            <Link href="/" className="brand">
              Protect Lassen · Herbicide Tracker
            </Link>
            <nav>
              <Link href="/herbicide-tracker">Applications</Link>
              <Link href="/map">Map</Link>
              <Link href="/chemical">Chemicals</Link>
              <Link href="/near-me">Search near an address</Link>
              <Link href="/about">About the data</Link>
            </nav>
          </div>
        </header>
        <main className="wrap">{children}</main>
        <footer className="site">
          <div className="wrap">
            <p>
              Built from public records obtained from California county agricultural
              commissioners. Every figure on this site comes from a source document;
              each application page lists the records it was built from.
            </p>
            <p>
              Parcel outlines show property associated with an application. They are not
              a measurement of the area actually treated.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
