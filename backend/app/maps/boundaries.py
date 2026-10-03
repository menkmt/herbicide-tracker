"""Reading a project boundary — a THP or other forestry unit — from a file.

Accepts what foresters and CAL FIRE actually hand out:

* KML / KMZ (Google Earth),
* GeoJSON,
* a zipped shapefile (.shp with its .dbf, .shx and .prj; CAL FIRE's own
  layers are usually in California Teale Albers, EPSG:3310, which the .prj
  says and this reprojects from).

Returns one MultiPolygon in longitude/latitude (EPSG:4326). Lines and points
are ignored: a boundary is an area.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import transform, unary_union

BOUNDARY_SUFFIXES = {".kml", ".kmz", ".geojson", ".json", ".zip"}
DOCUMENT_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif"}
CA_BOUNDS = (-124.6, 32.4, -114.0, 42.1)


class BoundaryError(ValueError):
    pass


def _polygons(geom) -> list[Polygon]:
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    if hasattr(geom, "geoms"):
        out: list[Polygon] = []
        for g in geom.geoms:
            out.extend(_polygons(g))
        return out
    return []


def _finish(polys: list[Polygon], source: str) -> MultiPolygon:
    polys = [p.buffer(0) if not p.is_valid else p for p in polys if not p.is_empty]
    if not polys:
        raise BoundaryError(f"{source}: no polygon boundary found in the file")
    merged = unary_union(polys)
    result = MultiPolygon(_polygons(merged))
    minx, miny, maxx, maxy = result.bounds
    w, s, e, n = CA_BOUNDS
    if maxx < w or minx > e or maxy < s or miny > n:
        raise BoundaryError(
            f"{source}: the boundary is not in California (it may be in a projection "
            "this file does not declare)"
        )
    return result


def _kml_polygons(xml_bytes: bytes) -> list[Polygon]:
    root = ElementTree.fromstring(xml_bytes)
    polys = []
    for poly in root.iter():
        if not poly.tag.endswith("Polygon"):
            continue
        rings = []
        for el in poly.iter():
            if el.tag.endswith("coordinates") and el.text:
                pts = []
                for chunk in el.text.split():
                    parts = chunk.split(",")
                    if len(parts) >= 2:
                        pts.append((float(parts[0]), float(parts[1])))
                if len(pts) >= 3:
                    rings.append(pts)
        if rings:
            polys.append(Polygon(rings[0], rings[1:]))
    return polys


def _shapefile_polygons(archive: zipfile.ZipFile) -> list[Polygon]:
    import shapefile
    from pyproj import CRS, Transformer

    names = archive.namelist()
    shp = next((n for n in names if n.lower().endswith(".shp") and "__MACOSX" not in n), None)
    if shp is None:
        raise BoundaryError("the zip has no .shp file")
    stem = shp[:-4]

    def member(ext: str) -> io.BytesIO | None:
        match = next((n for n in names if n.lower() == (stem + ext).lower()), None)
        return io.BytesIO(archive.read(match)) if match else None

    reader = shapefile.Reader(shp=member(".shp"), shx=member(".shx"), dbf=member(".dbf"))
    prj = member(".prj")
    project = None
    if prj is not None:
        crs = CRS.from_wkt(prj.read().decode("utf-8", errors="replace"))
        if not crs.equals(CRS.from_epsg(4326)) and not crs.is_geographic:
            project = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True).transform
    polys: list[Polygon] = []
    for item in reader.shapes():
        geom = shape(item.__geo_interface__)
        if project is not None:
            geom = transform(project, geom)
        polys.extend(_polygons(geom))
    return polys


def read_boundary(path: Path) -> MultiPolygon:
    suffix = path.suffix.lower()
    name = path.name
    if suffix == ".kml":
        return _finish(_kml_polygons(path.read_bytes()), name)
    if suffix == ".kmz":
        with zipfile.ZipFile(path) as z:
            kml = next((n for n in z.namelist() if n.lower().endswith(".kml")), None)
            if kml is None:
                raise BoundaryError(f"{name}: the KMZ has no KML inside")
            return _finish(_kml_polygons(z.read(kml)), name)
    if suffix in (".geojson", ".json"):
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        features = data.get("features") if data.get("type") == "FeatureCollection" else [data]
        polys: list[Polygon] = []
        for f in features or []:
            geometry = f.get("geometry", f) if isinstance(f, dict) else None
            if geometry:
                polys.extend(_polygons(shape(geometry)))
        return _finish(polys, name)
    if suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            if any(n.lower().endswith(".shp") for n in z.namelist()):
                return _finish(_shapefile_polygons(z), name)
            kml = next((n for n in z.namelist() if re.search(r"\.km[lz]$", n, re.I)), None)
            if kml:
                return _finish(_kml_polygons(z.read(kml)), name)
        raise BoundaryError(f"{name}: no shapefile or KML found in the zip")
    raise BoundaryError(
        f"{name}: not a boundary file (use KML, KMZ, GeoJSON or a zipped shapefile)"
    )


def to_geojson(geom: MultiPolygon) -> str:
    return json.dumps(mapping(geom))
