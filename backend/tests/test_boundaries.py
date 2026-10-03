import json
import zipfile

import pytest

from app.maps.boundaries import BoundaryError, read_boundary

KML = """<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>
<name>THP 2-23-00045-LAS</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
-120.90,40.30,0 -120.85,40.30,0 -120.85,40.34,0 -120.90,40.34,0 -120.90,40.30,0
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>"""


def test_kml_and_kmz(tmp_path):
    kml = tmp_path / "unit.kml"
    kml.write_text(KML)
    geom = read_boundary(kml)
    assert geom.geom_type == "MultiPolygon" and geom.area > 0
    kmz = tmp_path / "unit.kmz"
    with zipfile.ZipFile(kmz, "w") as z:
        z.writestr("doc.kml", KML)
    assert read_boundary(kmz).equals(geom)


def test_geojson(tmp_path):
    gj = tmp_path / "unit.geojson"
    gj.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [
            [[-120.9, 40.3], [-120.85, 40.3], [-120.85, 40.34], [-120.9, 40.3]]]}}]}))
    assert read_boundary(gj).geom_type == "MultiPolygon"


def test_shapefile_in_teale_albers_is_reprojected(tmp_path):
    import shapefile
    from pyproj import CRS, Transformer

    to_albers = Transformer.from_crs(4326, 3310, always_xy=True).transform
    ring = [to_albers(x, y) for x, y in
            [(-120.9, 40.3), (-120.85, 40.3), (-120.85, 40.34), (-120.9, 40.34), (-120.9, 40.3)]]
    stem = tmp_path / "thp"
    w = shapefile.Writer(str(stem), shapeType=shapefile.POLYGON)
    w.field("THP", "C")
    w.poly([ring])
    w.record("2-23-00045-LAS")
    w.close()
    (tmp_path / "thp.prj").write_text(CRS.from_epsg(3310).to_wkt())
    archive = tmp_path / "thp.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for ext in ("shp", "shx", "dbf", "prj"):
            z.write(tmp_path / f"thp.{ext}", f"thp.{ext}")
    minx, miny, maxx, maxy = read_boundary(archive).bounds
    assert -120.91 < minx < -120.89 and 40.29 < miny < 40.31


def test_boundary_outside_california_is_refused(tmp_path):
    bad = tmp_path / "bad.kml"
    bad.write_text(KML.replace("-120.", "10."))
    with pytest.raises(BoundaryError, match="not in California"):
        read_boundary(bad)
