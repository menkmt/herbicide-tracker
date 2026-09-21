import type { MetadataRoute } from "next";

/**
 * Public pages are indexable by design — being findable is the point of a
 * public record. Search and map endpoints are not: they are expensive,
 * infinite in combination, and of no value in an index.
 */
export default function robots(): MetadataRoute.Robots {
  const base = process.env.NEXT_PUBLIC_SITE_URL ?? "https://example.org";
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow: ["/api/", "/near-me?", "/herbicide-tracker?"],
      },
    ],
    sitemap: `${base}/sitemap.xml`,
  };
}
