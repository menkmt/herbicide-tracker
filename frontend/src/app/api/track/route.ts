import { NextResponse } from "next/server";

/**
 * Page-view beacon.
 *
 * The browser posts here (same origin, so a beacon works) and this forwards
 * to the tracker API with the visitor's address and browser attached for
 * the API to hash. The browser never talks to the API and the API never
 * stores what is attached here — see app/api/analytics.py.
 */
const API_BASE = process.env.TRACKER_API_URL ?? "http://localhost:8000";

export async function POST(request: Request) {
  let body: { path?: unknown; referrer?: unknown };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return new NextResponse(null, { status: 204 });
  }
  if (typeof body.path !== "string") return new NextResponse(null, { status: 204 });

  const forwarded = request.headers.get("x-forwarded-for") ?? "";
  const ip = forwarded.split(",")[0]?.trim() || request.headers.get("x-real-ip") || "0.0.0.0";
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Tracker-Client-IP": ip,
    "X-Tracker-Client-UA": request.headers.get("user-agent") ?? "",
  };
  // Present when a CDN in front sets it; otherwise simply absent.
  const country = request.headers.get("cf-ipcountry") ?? request.headers.get("x-country");
  if (country) headers["X-Tracker-Country"] = country;

  try {
    await fetch(`${API_BASE}/api/track`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        path: body.path.slice(0, 2048),
        referrer: typeof body.referrer === "string" ? body.referrer.slice(0, 2048) : null,
      }),
      cache: "no-store",
    });
  } catch {
    // Measurement never breaks the site.
  }
  return new NextResponse(null, { status: 204 });
}
