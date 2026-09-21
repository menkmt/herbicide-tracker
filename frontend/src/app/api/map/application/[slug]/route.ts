import { NextResponse } from "next/server";

/** GeoJSON for a single application's parcels. */
const API_BASE = process.env.TRACKER_API_URL ?? "http://localhost:8000";
const API_KEY = process.env.TRACKER_API_KEY;

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  const { slug } = await params;
  const headers: Record<string, string> = {};
  if (API_KEY) headers["X-API-Key"] = API_KEY;

  const response = await fetch(
    `${API_BASE}/api/map/applications?slug=${encodeURIComponent(slug)}&limit=200`,
    { headers, next: { revalidate: 300 } },
  );
  if (!response.ok) {
    return NextResponse.json({ type: "FeatureCollection", features: [] });
  }
  return NextResponse.json(await response.json());
}
