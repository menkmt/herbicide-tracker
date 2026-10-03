/** A THP / project map document, proxied so the browser never calls the API directly. */
const API_BASE = process.env.TRACKER_API_URL ?? "http://localhost:8000";
const API_KEY = process.env.TRACKER_API_KEY;

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) return new Response(null, { status: 404 });
  const headers: Record<string, string> = {};
  if (API_KEY) headers["X-API-Key"] = API_KEY;
  const response = await fetch(`${API_BASE}/api/project-documents/${id}`, {
    headers,
    next: { revalidate: 3600 },
  });
  if (!response.ok) return new Response(null, { status: 404 });
  return new Response(await response.arrayBuffer(), {
    headers: {
      "Content-Type": response.headers.get("Content-Type") ?? "application/octet-stream",
      "Content-Disposition": response.headers.get("Content-Disposition") ?? "inline",
      "Cache-Control": "public, max-age=3600",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
