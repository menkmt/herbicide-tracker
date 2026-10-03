import { NextResponse } from "next/server";

/** Water-monitoring stations for the map, proxied so the browser never calls the API. */
const API_BASE = process.env.TRACKER_API_URL ?? "http://localhost:8000";

export async function GET() {
  const response = await fetch(`${API_BASE}/api/map/water-stations`, { next: { revalidate: 600 } });
  if (!response.ok) return NextResponse.json({ type: "FeatureCollection", features: [] });
  return NextResponse.json(await response.json(), {
    headers: { "Cache-Control": "public, max-age=600" },
  });
}
