import type { MetadataRoute } from "next";

/**
 * Public pages are indexable by design — being findable is the point of a
 * public record. Search and map endpoints are not: they are expensive,
 * infinite in combination, and of no value in an index.
 */
// Rendered per request, not at build time, so the values come from the
// server's environment rather than whatever the build machine had.
export const dynamic = "force-dynamic";

export default function robots(): MetadataRoute.Robots {
  const base = (process.env.TRACKER_PUBLIC_BASE_URL ?? "https://example.org").replace(/\/$/, "");
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow: ["/api/", "/near-me?", "/applications?"],
      },
    ],
    sitemap: `${base}/sitemap.xml`,
  };
}
