"use client";

import maplibregl, { type Map as MapLibreMap } from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import "maplibre-gl/dist/maplibre-gl.css";

export interface ParcelMapProps {
  /** GeoJSON endpoint, or an inline FeatureCollection for a single application. */
  source: string | GeoJSON.FeatureCollection;
  /** Fit to these bounds instead of the loaded data. */
  bounds?: [number, number, number, number];
  tall?: boolean;
  /** Draw a search radius circle, in miles, around a point. */
  radius?: { lat: number; lon: number; miles: number };
}

/**
 * Pan/zoom map of application parcels, with clickable outlines.
 *
 * Parcels are drawn over satellite imagery. Clicking one opens a summary and a
 * link to the full application page; the popup is deliberately explicit that
 * the outline is the property, not the sprayed area.
 *
 * The basemap comes from configuration rather than being hard-coded, because
 * satellite imagery providers have licence terms that differ by deployment.
 */
export function ParcelMap({ source, bounds, tall, radius }: ParcelMapProps) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!container.current || mapRef.current) return;

    const styleUrl = process.env.NEXT_PUBLIC_BASEMAP_STYLE_URL;
    const tileUrl = process.env.NEXT_PUBLIC_BASEMAP_TILE_URL;
    const attribution = process.env.NEXT_PUBLIC_BASEMAP_ATTRIBUTION ?? "";

    if (!styleUrl && !tileUrl) {
      setError(
        "No basemap is configured. Set NEXT_PUBLIC_BASEMAP_TILE_URL (or _STYLE_URL) " +
          "to the aerial imagery service this deployment is licensed to use.",
      );
      return;
    }

    const map = new maplibregl.Map({
      container: container.current,
      style: styleUrl
        ? styleUrl
        : {
            version: 8,
            sources: {
              basemap: {
                type: "raster",
                tiles: [tileUrl as string],
                tileSize: 256,
                attribution,
              },
            },
            layers: [{ id: "basemap", type: "raster", source: "basemap" }],
          },
      center: [-120.65, 40.45],
      zoom: 8,
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }));

    map.on("load", async () => {
      let data: GeoJSON.FeatureCollection;
      if (typeof source === "string") {
        try {
          const response = await fetch(source);
          data = (await response.json()) as GeoJSON.FeatureCollection;
        } catch {
          setError("The parcel data could not be loaded.");
          return;
        }
      } else {
        data = source;
      }

      map.addSource("parcels", { type: "geojson", data });

      map.addLayer({
        id: "parcel-fill",
        type: "fill",
        source: "parcels",
        paint: {
          // Flagged applications read as red; everything else is neutral, so
          // colour only ever means one thing on this map.
          "fill-color": [
            "case",
            ["==", ["get", "flag_level"], "red"], "#b3261e",
            "#f2c94c",
          ],
          "fill-opacity": 0.22,
        },
      });
      map.addLayer({
        id: "parcel-line",
        type: "line",
        source: "parcels",
        paint: {
          "line-color": [
            "case",
            ["==", ["get", "flag_level"], "red"], "#8f1d17",
            "#e3b21f",
          ],
          "line-width": 2,
        },
      });

      if (radius) {
        map.addSource("radius", { type: "geojson", data: circle(radius) });
        map.addLayer({
          id: "radius-line",
          type: "line",
          source: "radius",
          paint: { "line-color": "#2f5d46", "line-width": 2, "line-dasharray": [2, 2] },
        });
        new maplibregl.Marker({ color: "#2f5d46" })
          .setLngLat([radius.lon, radius.lat])
          .addTo(map);
      }

      map.on("click", "parcel-fill", (event) => {
        const feature = event.features?.[0];
        if (!feature) return;
        const p = feature.properties as Record<string, string>;
        const flag = p.flag_headline
          ? `<div style="margin-top:6px"><strong>${escapeHtml(p.flag_headline)}</strong></div>`
          : "";
        new maplibregl.Popup({ maxWidth: "300px" })
          .setLngLat(event.lngLat)
          .setHTML(
            `<div><strong>${escapeHtml(p.title ?? "Application")}</strong></div>` +
              `<div>${escapeHtml(p.date ?? "")}${p.acres ? ` · ${p.acres} acres reported` : ""}</div>` +
              (p.apn ? `<div>Parcel ${escapeHtml(p.apn)}</div>` : "") +
              flag +
              `<div style="margin-top:6px;font-size:.82em;color:#55635c">` +
              `Outline is the property associated with this application, not the ` +
              `sprayed area.</div>` +
              `<div style="margin-top:8px"><a href="${escapeHtml(p.url ?? "#")}">` +
              `View full application →</a></div>`,
          )
          .addTo(map);
      });

      map.on("mouseenter", "parcel-fill", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "parcel-fill", () => {
        map.getCanvas().style.cursor = "";
      });

      if (bounds) {
        map.fitBounds(bounds, { padding: 48, maxZoom: 15 });
      } else if (data.features.length > 0) {
        map.fitBounds(featureBounds(data), { padding: 48, maxZoom: 15 });
      }
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [source, bounds, radius]);

  if (error) {
    return (
      <div className={`map ${tall ? "tall" : ""}`} style={{ display: "grid", placeItems: "center", padding: 24 }}>
        <p className="muted small" style={{ maxWidth: "48ch", textAlign: "center" }}>
          {error}
        </p>
      </div>
    );
  }

  return <div ref={container} className={`map ${tall ? "tall" : ""}`} />;
}

function featureBounds(data: GeoJSON.FeatureCollection): [number, number, number, number] {
  let minX = 180;
  let minY = 90;
  let maxX = -180;
  let maxY = -90;
  const visit = (coords: unknown): void => {
    if (typeof (coords as number[])[0] === "number") {
      const [x, y] = coords as number[];
      minX = Math.min(minX, x);
      maxX = Math.max(maxX, x);
      minY = Math.min(minY, y);
      maxY = Math.max(maxY, y);
      return;
    }
    for (const child of coords as unknown[]) visit(child);
  };
  for (const feature of data.features) {
    if (feature.geometry && "coordinates" in feature.geometry) {
      visit(feature.geometry.coordinates);
    }
  }
  return [minX, minY, maxX, maxY];
}

/** Approximate a radius circle for display; the search itself is done in PostGIS. */
function circle({ lat, lon, miles }: { lat: number; lon: number; miles: number }) {
  const points: [number, number][] = [];
  const radiusKm = miles * 1.609344;
  const latOffset = radiusKm / 110.574;
  const lonOffset = radiusKm / (111.32 * Math.cos((lat * Math.PI) / 180));
  for (let i = 0; i <= 64; i += 1) {
    const angle = (i / 64) * 2 * Math.PI;
    points.push([lon + lonOffset * Math.cos(angle), lat + latOffset * Math.sin(angle)]);
  }
  return {
    type: "FeatureCollection",
    features: [
      { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: points } },
    ],
  } as GeoJSON.FeatureCollection;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char] ?? char,
  );
}
