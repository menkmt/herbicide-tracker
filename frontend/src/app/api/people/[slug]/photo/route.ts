/** A person's photo, proxied so the browser never calls the API directly. */
const API_BASE = process.env.TRACKER_API_URL ?? "http://localhost:8000";

export async function GET(_request: Request, { params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const response = await fetch(`${API_BASE}/api/people/${encodeURIComponent(slug)}/photo`, {
    next: { revalidate: 3600 },
  });
  if (!response.ok) return new Response(null, { status: 404 });
  return new Response(await response.arrayBuffer(), {
    headers: { "Content-Type": "image/jpeg", "Cache-Control": "public, max-age=3600" },
  });
}
