import { NextResponse } from "next/server";

/**
 * Proxy for the map's GeoJSON feed.
 *
 * The browser never talks to the tracker API directly: routing map data through
 * the site keeps the API's address and any subscriber key server-side, and gives
 * one place to cap how much geometry a single request can pull.
 */
const API_BASE = process.env.TRACKER_API_URL ?? "http://localhost:8000";
const API_KEY = process.env.TRACKER_API_KEY;

export async function GET(request: Request) {
  const incoming = new URL(request.url);
  const query = new URLSearchParams();
  for (const key of ["county", "year", "bbox"]) {
    const value = incoming.searchParams.get(key);
    if (value) query.set(key, value);
  }
  query.set("limit", "2000");

  const headers: Record<string, string> = {};
  if (API_KEY) headers["X-API-Key"] = API_KEY;

  const response = await fetch(`${API_BASE}/api/map/applications?${query}`, {
    headers,
    next: { revalidate: 300 },
  });
  if (!response.ok) {
    return NextResponse.json({ type: "FeatureCollection", features: [] }, { status: 200 });
  }
  return NextResponse.json(await response.json(), {
    headers: { "Cache-Control": "public, max-age=300" },
  });
}
