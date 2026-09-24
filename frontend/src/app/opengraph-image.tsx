import { ImageResponse } from "next/og";

/**
 * Default link-preview image, generated on the server so it always matches
 * the site's name and look without a designer in the loop.
 */
export const alt = "Herbicide Tracker California";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: 72,
          background: "linear-gradient(135deg, #0a0e1a 0%, #131a2e 60%, #1a2340 100%)",
          color: "#e7ebf6",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
          <div
            style={{
              width: 64,
              height: 64,
              borderRadius: 18,
              background: "linear-gradient(135deg, #6d8dff, #a78bfa)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#fff",
              fontSize: 28,
              fontWeight: 800,
            }}
          >
            HT
          </div>
          <div style={{ display: "flex", gap: 12, fontSize: 36, fontWeight: 700 }}>
            <span>Herbicide Tracker</span>
            <span style={{ color: "#98a3c0", fontWeight: 400 }}>California</span>
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <div style={{ fontSize: 72, fontWeight: 800, lineHeight: 1.05, letterSpacing: -2 }}>
            Every forestry herbicide application, on the record.
          </div>
          <div style={{ fontSize: 30, color: "#98a3c0" }}>
            What was sprayed, where, by whom and how much — from county public records.
          </div>
        </div>
      </div>
    ),
    size,
  );
}
