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
 * Basemaps offered in the switcher.
 *
 * The USGS layers are public domain and safe for a paid product. Esri, CARTO
 * and OpenTopoMap restrict commercial use under their free terms; before the
 * site is sold, either license them or delete their entries here and the
 * switcher simply offers fewer choices.
 */
interface Basemap {
  id: string;
  label: string;
  tiles: string[];
  attribution: string;
  maxzoom: number;
}

const BASEMAPS: Basemap[] = [
  {
    id: "esri-imagery",
    label: "Satellite",
    tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
    attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
    maxzoom: 19,
  },
  {
    id: "usgs-imagery",
    label: "Satellite (USGS)",
    tiles: ["https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}"],
    attribution: "USGS The National Map",
    maxzoom: 16,
  },
  {
    id: "carto-streets",
    label: "Streets",
    tiles: ["a", "b", "c", "d"].map(
      (s) => `https://${s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png`,
    ),
    attribution: "© OpenStreetMap contributors © CARTO",
    maxzoom: 19,
  },
  {
    id: "opentopo",
    label: "Topographic",
    tiles: ["a", "b", "c"].map((s) => `https://${s}.tile.opentopomap.org/{z}/{x}/{y}.png`),
    attribution: "© OpenTopoMap (CC-BY-SA) © OpenStreetMap contributors",
    maxzoom: 17,
  },
  {
    id: "usgs-topo",
    label: "Topographic (USGS)",
    tiles: ["https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}"],
    attribution: "USGS The National Map",
    maxzoom: 16,
  },
];

/** Overlays drawn above the basemap and below the applications. */
interface Overlay {
  id: string;
  label: string;
  tiles: string[];
  attribution: string;
  opacity: number;
  defaultOn: boolean;
  maxzoom: number;
}

const OVERLAYS: Overlay[] = [
  {
    // Streams, rivers and lakes: on by default, because on a herbicide map
    // the question after "where" is "near what water".
    id: "hydro",
    label: "Streams & water",
    tiles: ["https://basemap.nationalmap.gov/arcgis/rest/services/USGSHydroCached/MapServer/tile/{z}/{y}/{x}"],
    attribution: "USGS National Hydrography",
    opacity: 0.9,
    defaultOn: true,
    maxzoom: 16,
  },
  {
    // Who manages the land: national forest, BLM, state, private.
    id: "ownership",
    label: "Land ownership",
    tiles: ["https://gis.blm.gov/arcgis/rest/services/lands/BLM_Natl_SMA_Cached_without_PriUnk/MapServer/tile/{z}/{y}/{x}"],
    attribution: "BLM Surface Management Agency",
    opacity: 0.45,
    defaultOn: false,
    maxzoom: 14,
  },
];

const CA_CENTRE: [number, number] = [-120.6, 39.6];

/**
 * Pan/zoom map of applications.
 *
 * Applications with identified parcels are drawn as solid parcel outlines.
 * Applications whose parcels are not identified yet are drawn as their
 * reported section — the square mile on the use report — with a dashed edge
 * and a lighter fill, and the popup says plainly which one it is.
 */
export function ParcelMap({ source, bounds, tall, radius }: ParcelMapProps) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [count, setCount] = useState<number | null>(null);
  const [basemap, setBasemap] = useState(BASEMAPS[0].id);
  const [overlays, setOverlays] = useState<Record<string, boolean>>(
    Object.fromEntries(OVERLAYS.map((o) => [o.id, o.defaultOn])),
  );
  const [stationsOn, setStationsOn] = useState(true);
  const [panelOpen, setPanelOpen] = useState(false);
  const [keyOpen, setKeyOpen] = useState(false);

  useEffect(() => {
    if (!container.current || mapRef.current) return;

    const sources: maplibregl.StyleSpecification["sources"] = {};
    const layers: maplibregl.LayerSpecification[] = [];
    for (const b of BASEMAPS) {
      sources[b.id] = { type: "raster", tiles: b.tiles, tileSize: 256, attribution: b.attribution, maxzoom: b.maxzoom };
      layers.push({
        id: b.id, type: "raster", source: b.id,
        layout: { visibility: b.id === BASEMAPS[0].id ? "visible" : "none" },
      });
    }
    for (const o of OVERLAYS) {
      sources[o.id] = { type: "raster", tiles: o.tiles, tileSize: 256, attribution: o.attribution, maxzoom: o.maxzoom };
      layers.push({
        id: o.id, type: "raster", source: o.id,
        paint: { "raster-opacity": o.opacity },
        layout: { visibility: o.defaultOn ? "visible" : "none" },
      });
    }

    const map = new maplibregl.Map({
      container: container.current,
      style: { version: 8, sources, layers },
      center: CA_CENTRE,
      zoom: 6,
      attributionControl: { compact: true },
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }));
    map.addControl(new maplibregl.FullscreenControl(), "top-right");

    // Start on the style, not on "load": "load" waits for every basemap tile,
    // so one slow or unreachable tile server would keep the applications from
    // ever being drawn. The style here is inline and ready almost at once.
    let started = false;
    const start = async () => {
      if (started) return;
      started = true;
      let data: GeoJSON.FeatureCollection;
      if (typeof source === "string") {
        try {
          const response = await fetch(source);
          data = (await response.json()) as GeoJSON.FeatureCollection;
        } catch {
          setError("The map data could not be loaded.");
          return;
        }
      } else {
        data = source;
      }
      setCount(
        new Set(data.features.map((f) => f.properties?.cluster_id).filter((id) => id != null)).size,
      );

      map.addSource("apps", { type: "geojson", data });

      const isSection = ["==", ["get", "geometry_kind"], "section"] as maplibregl.ExpressionSpecification;
      const isRed = ["==", ["get", "flag_level"], "red"] as maplibregl.ExpressionSpecification;
      const isProject = ["==", ["get", "geometry_kind"], "project"] as maplibregl.ExpressionSpecification;

      // THP / project units: a violet dashed edge under the applications, so
      // they read as context — where the work was permitted — not as spraying.
      map.addLayer({
        id: "projects-fill",
        type: "fill",
        source: "apps",
        filter: isProject,
        paint: { "fill-color": "#a78bfa", "fill-opacity": 0.06 },
      });
      map.addLayer({
        id: "projects-line",
        type: "line",
        source: "apps",
        filter: isProject,
        paint: { "line-color": "#a78bfa", "line-width": 2.4, "line-dasharray": [3, 2] },
      });

      map.addLayer({
        id: "apps-fill",
        type: "fill",
        source: "apps",
        filter: ["!", isProject],
        paint: {
          // Colour only ever means a warning: red for flagged, amber otherwise.
          "fill-color": ["case", isRed, "#ff6b6b", "#ffb156"],
          "fill-opacity": ["case", isSection, 0.12, 0.3],
        },
      });
      map.addLayer({
        id: "apps-line-parcel",
        type: "line",
        source: "apps",
        filter: ["all", ["!", isSection], ["!", isProject]],
        paint: { "line-color": ["case", isRed, "#ff6b6b", "#ffb156"], "line-width": 2.2 },
      });
      map.addLayer({
        id: "apps-line-section",
        type: "line",
        source: "apps",
        filter: isSection,
        paint: {
          "line-color": ["case", isRed, "#ff6b6b", "#ffb156"],
          "line-width": 1.8,
          "line-dasharray": [2, 1.5],
        },
      });

      // Water monitoring: where water is sampled, and whether anyone tests it
      // for herbicides. Blue, and a different shape, so it never reads as an
      // application.
      try {
        const stations = (await (await fetch("/api/map/water-stations")).json()) as GeoJSON.FeatureCollection;
        map.addSource("stations", { type: "geojson", data: stations });
        map.addLayer({
          id: "stations",
          type: "circle",
          source: "stations",
          paint: {
            "circle-radius": 6,
            "circle-color": [
              "case",
              ["==", ["get", "herbicides_tested"], true], "#22d3ee",
              "#3b82f6",
            ],
            "circle-stroke-color": "#ffffff",
            "circle-stroke-width": 2,
          },
        });
        map.on("click", "stations", (event) => {
          const f = event.features?.[0];
          if (!f) return;
          const p = f.properties as Record<string, string | boolean | null>;
          const tested =
            p.herbicides_tested === true ? "Tested for herbicides"
            : p.herbicides_tested === false ? "<strong>Not tested for any herbicide</strong>"
            : "Herbicide testing not reported";
          new maplibregl.Popup({ maxWidth: "280px" })
            .setLngLat(event.lngLat)
            .setHTML(
              `<div style="font-weight:700">${escapeHtml(String(p.name ?? "Monitoring station"))}</div>` +
                (p.operator ? `<div>${escapeHtml(String(p.operator))}</div>` : "") +
                `<div style="margin-top:6px">${tested}</div>` +
                (p.analytes_note ? `<div style="font-size:.85em;opacity:.8">${escapeHtml(String(p.analytes_note))}</div>` : "") +
                (p.note ? `<div style="margin-top:4px;font-size:.85em">${escapeHtml(String(p.note))}</div>` : "") +
                (p.approximate ? `<div style="font-size:.8em;opacity:.7">Location approximate.</div>` : "") +
                (p.source_url ? `<div style="margin-top:6px"><a href="${escapeHtml(String(p.source_url))}" rel="nofollow noopener">Source</a></div>` : ""),
            )
            .addTo(map);
        });
        map.on("mouseenter", "stations", () => { map.getCanvas().style.cursor = "pointer"; });
        map.on("mouseleave", "stations", () => { map.getCanvas().style.cursor = ""; });
      } catch {
        // The stations layer is optional; the map works without it.
      }

      if (radius) {
        map.addSource("radius", { type: "geojson", data: circle(radius) });
        map.addLayer({
          id: "radius-line",
          type: "line",
          source: "radius",
          paint: { "line-color": "#22d3ee", "line-width": 2, "line-dasharray": [2, 2] },
        });
        new maplibregl.Marker({ color: "#22d3ee" }).setLngLat([radius.lon, radius.lat]).addTo(map);
      }

      map.on("click", "apps-fill", (event) => {
        const feature = event.features?.[0];
        if (!feature) return;
        const p = feature.properties as Record<string, string>;
        const section = p.geometry_kind === "section";
        const inUnit = p.geometry_kind === "project_area";
        const flag = p.flag_headline
          ? `<div style="margin-top:6px;color:#ff8a8a"><strong>${escapeHtml(p.flag_headline)}</strong></div>`
          : "";
        const where = section
          ? `<div>Reported section ${escapeHtml(p.mtrs ?? "")}</div>` +
            (p.land ? `<div>${escapeHtml(p.land)} <span style="opacity:.7">(at the section's centre)</span></div>` : "")
          : p.apn ? `<div>Parcel ${escapeHtml(p.apn)}</div>` : "";
        const caveat = section
          ? "Dashed square is the one-square-mile section the use report names. " +
            "The property inside it has not been identified yet; this is not the sprayed area."
          : inUnit
            ? "Shaded area is the part of the THP / project unit inside the reported section — " +
              "where the work was permitted, not the exact area sprayed."
            : "Outline is the property associated with this application, not the sprayed area.";
        new maplibregl.Popup({ maxWidth: "300px" })
          .setLngLat(event.lngLat)
          .setHTML(
            `<div style="font-weight:700">${escapeHtml(p.title ?? "Application")}</div>` +
              (p.owner && p.owner !== p.title ? `<div>${escapeHtml(p.owner)}</div>` : "") +
              `<div>${escapeHtml(p.date ?? "")}${p.acres ? ` · ${escapeHtml(String(p.acres))} acres reported` : ""}` +
              `${p.method === "aerial" ? " · Aerial" : p.method === "ground" ? " · Ground" : ""}</div>` +
              where +
              flag +
              `<div style="margin-top:6px;font-size:.8em;opacity:.75">${caveat}</div>` +
              `<div style="margin-top:8px"><a href="${escapeHtml(p.url ?? "#")}">View full application →</a></div>`,
          )
          .addTo(map);
      });
      map.on("click", "projects-fill", (event) => {
        // An application drawn inside the unit has its own popup.
        if (map.queryRenderedFeatures(event.point, { layers: ["apps-fill"] }).length) return;
        const feature = event.features?.[0];
        if (!feature) return;
        const p = feature.properties as Record<string, string | number>;
        let apps: Array<{ slug: string; title: string; date: string | null }> = [];
        try { apps = JSON.parse(String(p.applications ?? "[]")); } catch { apps = []; }
        const more = Number(p.application_count ?? apps.length) - apps.length;
        new maplibregl.Popup({ maxWidth: "300px" })
          .setLngLat(event.lngLat)
          .setHTML(
            `<div style="font-weight:700">${escapeHtml(String(p.title ?? "Project"))}</div>` +
              (p.name ? `<div>${escapeHtml(String(p.name))}</div>` : "") +
              `<div style="margin-top:6px;font-size:.85em;opacity:.8">Applications in this unit:</div>` +
              apps.map((a) =>
                `<div><a href="/application/${encodeURIComponent(a.slug)}">${escapeHtml(a.title)}</a>` +
                `${a.date ? ` <span style="opacity:.7">${escapeHtml(a.date)}</span>` : ""}</div>`).join("") +
              (more > 0 ? `<div style="opacity:.7">and ${more} more</div>` : "") +
              `<div style="margin-top:6px;font-size:.8em;opacity:.75">Dashed violet line is the unit boundary from the project map.</div>`,
          )
          .addTo(map);
      });
      map.on("mouseenter", "projects-fill", () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", "projects-fill", () => { map.getCanvas().style.cursor = ""; });
      map.on("mouseenter", "apps-fill", () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", "apps-fill", () => { map.getCanvas().style.cursor = ""; });

      if (bounds) {
        map.fitBounds(bounds, { padding: 48, maxZoom: 15 });
      } else if (data.features.length > 0) {
        map.fitBounds(featureBounds(data), { padding: 48, maxZoom: 14, duration: 0 });
      }
    };
    if (map.isStyleLoaded()) void start();
    else map.on("styledata", () => void start());

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [source, bounds, radius]);

  // Basemap and overlay switching only flips layer visibility, so the data
  // layers and any open popup are untouched.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      if (map.getLayer("stations")) map.setLayoutProperty("stations", "visibility", stationsOn ? "visible" : "none");
      for (const b of BASEMAPS) {
        if (map.getLayer(b.id)) map.setLayoutProperty(b.id, "visibility", b.id === basemap ? "visible" : "none");
      }
      for (const o of OVERLAYS) {
        if (map.getLayer(o.id)) map.setLayoutProperty(o.id, "visibility", overlays[o.id] ? "visible" : "none");
      }
    };
    if (map.isStyleLoaded()) apply();
    else map.once("styledata", apply);
  }, [basemap, overlays, stationsOn]);

  if (error) {
    return (
      <div className={`map ${tall ? "tall" : ""}`} style={{ display: "grid", placeItems: "center", padding: 24 }}>
        <p className="muted small" style={{ maxWidth: "48ch", textAlign: "center" }}>{error}</p>
      </div>
    );
  }

  return (
    <div className={`map-wrap ${tall ? "tall" : ""}`}>
      <div ref={container} className={`map ${tall ? "tall" : ""}`} />

      <div className="map-panel">
        <button type="button" className="map-panel-toggle" onClick={() => setPanelOpen((v) => !v)}
                aria-expanded={panelOpen}>
          Layers {panelOpen ? "▾" : "▸"}
        </button>
        {panelOpen && (
          <div className="map-panel-body">
            <div className="map-panel-head">Basemap</div>
            {BASEMAPS.map((b) => (
              <label key={b.id}>
                <input type="radio" name="basemap" checked={basemap === b.id} onChange={() => setBasemap(b.id)} />
                {b.label}
              </label>
            ))}
            <div className="map-panel-head">Overlays</div>
            {OVERLAYS.map((o) => (
              <label key={o.id}>
                <input type="checkbox" checked={!!overlays[o.id]}
                       onChange={() => setOverlays((s) => ({ ...s, [o.id]: !s[o.id] }))} />
                {o.label}
              </label>
            ))}
            <label>
              <input type="checkbox" checked={stationsOn} onChange={() => setStationsOn((v) => !v)} />
              Water monitoring stations
            </label>
          </div>
        )}
      </div>

      <div className="map-key">
        <button type="button" className="map-panel-toggle" onClick={() => setKeyOpen((v) => !v)}
                aria-expanded={keyOpen}>
          Map key {keyOpen ? "▾" : "▸"}
        </button>
        {keyOpen && (
          <div className="map-panel-body">
            <div><span className="sw sw-parcel" /> Application, property identified</div>
            <div><span className="sw sw-section" /> Application, reported section only</div>
            <div><span className="sw sw-project" /> THP / project unit</div>
            <div><span className="sw sw-red" /> Restricted or watch-listed chemical</div>
            <div><span className="sw sw-water" /> Streams &amp; water (USGS)</div>
            <div><span className="sw sw-station" /> Water monitoring station</div>
            <p className="small muted" style={{ margin: "8px 0 0" }}>
              Outlines show property or the reported square mile — never the area sprayed.
            </p>
          </div>
        )}
      </div>

      {count === 0 && (
        <div className="map-empty">No published applications in this view yet.</div>
      )}
    </div>
  );
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
