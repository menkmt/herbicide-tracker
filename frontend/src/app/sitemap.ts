import type { MetadataRoute } from "next";
import { api } from "@/lib/api";
import { CALIFORNIA_COUNTIES, countySlug } from "@/lib/counties";

/** Public pages are meant to be indexable, so they are listed properly. */
// Rendered per request, not at build time, so the values come from the
// server's environment rather than whatever the build machine had.
export const dynamic = "force-dynamic";

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const base = (process.env.TRACKER_PUBLIC_BASE_URL ?? "https://example.org").replace(/\/$/, "");
  const entries: MetadataRoute.Sitemap = [
    { url: `${base}/`, changeFrequency: "weekly", priority: 1 },
    { url: `${base}/applications`, changeFrequency: "weekly", priority: 0.9 },
    { url: `${base}/counties`, changeFrequency: "weekly", priority: 0.8 },
    { url: `${base}/support`, changeFrequency: "monthly", priority: 0.4 },
    { url: `${base}/contact`, changeFrequency: "yearly", priority: 0.4 },
    { url: `${base}/map`, changeFrequency: "weekly", priority: 0.7 },
    { url: `${base}/chemical`, changeFrequency: "monthly", priority: 0.7 },
    { url: `${base}/about`, changeFrequency: "yearly", priority: 0.5 },
  ];

  // Every county has a page whether or not it has records yet.
  let publishedSlugs = new Set<string>();
  try {
    const { counties } = await api.counties();
    publishedSlugs = new Set(counties.map((c) => c.slug));
  } catch {
    // Listed below regardless.
  }
  for (const name of CALIFORNIA_COUNTIES) {
    const slug = countySlug(name);
    entries.push({
      url: `${base}/applications/${slug}`,
      changeFrequency: publishedSlugs.has(slug) ? "weekly" : "monthly",
      priority: publishedSlugs.has(slug) ? 0.8 : 0.5,
    });
  }

  try {
    const { chemicals } = await api.chemicals();
    for (const chemical of chemicals) {
      entries.push({ url: `${base}${chemical.url}`, changeFrequency: "monthly", priority: 0.6 });
    }
    // Paged so a large tracker does not try to build one enormous sitemap.
    const first = await api.applications({ page_size: 50 });
    for (let page = 1; page <= Math.min(first.pages, 40); page += 1) {
      const data = page === 1 ? first : await api.applications({ page, page_size: 50 });
      for (const application of data.applications) {
        entries.push({
          url: `${base}${application.url}`,
          lastModified: application.date_end ?? undefined,
          changeFrequency: "yearly",
          priority: 0.6,
        });
      }
    }
  } catch {
    // A sitemap that is missing entries is better than a build that fails.
  }
  return entries;
}
