/**
 * Structured data for search engines.
 *
 * Google reads schema.org JSON-LD to understand what a page is: that the
 * site has a search box, that an application page sits under a county, and
 * so on. It is data about the page, never content, so nothing here says
 * anything the visible page does not.
 */
export function JsonLd({ data }: { data: Record<string, unknown> }) {
  return (
    <script
      type="application/ld+json"
      // JSON with "<" escaped so a value can never close the script element.
      dangerouslySetInnerHTML={{ __html: JSON.stringify(data).replace(/</g, "\\u003c") }}
    />
  );
}

export function siteBase(): string {
  return (process.env.TRACKER_PUBLIC_BASE_URL ?? "https://example.org").replace(/\/$/, "");
}

/** Breadcrumb trail as schema.org BreadcrumbList. `items` are [name, path]. */
export function breadcrumbs(items: Array<[string, string]>): Record<string, unknown> {
  const base = siteBase();
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: items.map(([name, path], index) => ({
      "@type": "ListItem",
      position: index + 1,
      name,
      item: `${base}${path}`,
    })),
  };
}
