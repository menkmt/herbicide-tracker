#!/usr/bin/env python3
"""Probe the external GIS services and report what they actually return.

The tracker's geospatial providers are written against the documented ArcGIS
REST contract, but every county publishes its parcels under different field
names, and a layer index that was right last year may not be right now. Rather
than guess, this script asks each service what it holds and prints the field
names the provider needs.

Run it from an environment with network access:

    python -m scripts.probe_gis
    python -m scripts.probe_gis --county lassen --search-parcels

Nothing here writes to the database or changes configuration. It reports, and
the report is what gets pasted into ``app/providers/parcels/registry.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx

TIMEOUT = 30.0

#: Services the tracker depends on, and what we need to learn from each.
SERVICES: dict[str, dict[str, str]] = {
    "plss": {
        "url": "https://gis.blm.gov/arcgis/rest/services/Cadastral/"
               "BLM_Natl_PLSS_CadNSDI/MapServer",
        "needs": "the layer index holding first-division (section) polygons, and the "
                 "township/range/section field names",
    },
    "calfire": {
        "url": "https://egis.fire.ca.gov/arcgis/rest/services/FRAP/ForestPractice/MapServer",
        "needs": "layer indices for THPs, exemptions and NTMPs, and the plan number, "
                 "plan name and landowner field names",
    },
    "naip": {
        "url": "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/"
               "MapServer",
        "needs": "confirmation the imagery tile endpoint is reachable and its tile "
                 "URL template",
    },
}

#: Field names a parcel layer might use, so the probe can suggest a mapping.
APN_CANDIDATES = ("APN", "PARCEL_APN", "PARCELID", "PARCEL_ID", "APN_D", "ASSESSOR_PARCEL",
                  "APNLABEL", "PIN", "AIN", "MAPBLKLOT")
OWNER_CANDIDATES = ("OWNER", "OWNER_NAME", "OWNERNAME", "OWN_NAME", "OWNER1", "TAXPAYER",
                    "ASSESSEE", "OWNER_FULL")
ACRE_CANDIDATES = ("ACRES", "ACREAGE", "GIS_ACRES", "SHAPE_ACRES", "LEGAL_ACRE", "AREA_AC")
ADDRESS_CANDIDATES = ("SITUS_ADDR", "SITUS_ADDRESS", "SITUSADDR", "SITE_ADDR",
                      "PROPERTY_ADDRESS", "ADDRESS")


@dataclass
class ProbeResult:
    name: str
    url: str
    ok: bool
    detail: str = ""
    layers: list[dict[str, Any]] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    suggestion: dict[str, str] = field(default_factory=dict)


def _get(client: httpx.Client, url: str, **params: Any) -> Any:
    response = client.get(url, params={"f": "json", **params})
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and "error" in payload:
        raise RuntimeError(payload["error"].get("message", "service error"))
    return payload


def probe_service(client: httpx.Client, name: str, url: str) -> ProbeResult:
    """Ask a MapServer/FeatureServer what layers it publishes."""
    try:
        meta = _get(client, url)
    except Exception as exc:  # noqa: BLE001 - the point is to report failures
        return ProbeResult(name, url, ok=False, detail=f"{type(exc).__name__}: {exc}")

    layers = [
        {"id": layer.get("id"), "name": layer.get("name"), "type": layer.get("geometryType")}
        for layer in (meta.get("layers") or [])
    ]
    return ProbeResult(
        name,
        url,
        ok=True,
        detail=meta.get("serviceDescription") or meta.get("description") or "",
        layers=layers,
    )


def probe_layer(client: httpx.Client, url: str, layer_id: int) -> ProbeResult:
    """Read one layer's field names and suggest a parcel field mapping."""
    layer_url = f"{url.rstrip('/')}/{layer_id}"
    try:
        meta = _get(client, layer_url)
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(str(layer_id), layer_url, ok=False, detail=str(exc))

    names = [f.get("name", "") for f in (meta.get("fields") or [])]
    upper = {n.upper(): n for n in names}

    def pick(candidates: tuple[str, ...]) -> str | None:
        for candidate in candidates:
            if candidate in upper:
                return upper[candidate]
        # Fall back to a substring match, which catches county-specific prefixes.
        for key, original in upper.items():
            if any(candidate in key for candidate in candidates):
                return original
        return None

    suggestion = {
        "apn": pick(APN_CANDIDATES) or "",
        "owner": pick(OWNER_CANDIDATES) or "",
        "acreage": pick(ACRE_CANDIDATES) or "",
        "address": pick(ADDRESS_CANDIDATES) or "",
    }
    return ProbeResult(
        meta.get("name", str(layer_id)),
        layer_url,
        ok=True,
        detail=f"{meta.get('geometryType', 'unknown geometry')}, "
               f"{meta.get('maxRecordCount', '?')} max records per request",
        fields=names,
        suggestion=suggestion,
    )


def find_parcel_services(client: httpx.Client, query: str) -> list[dict[str, str]]:
    """Search ArcGIS Online for a county's published parcel service.

    Counties move their services, so searching beats a hard-coded URL that was
    right once. Results still need a human to confirm which is authoritative.
    """
    try:
        payload = _get(
            client,
            "https://www.arcgis.com/sharing/rest/search",
            q=f"{query} parcels type:\"Feature Service\"",
            num=10,
            sortField="numviews",
            sortOrder="desc",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  search failed: {exc}", file=sys.stderr)
        return []

    return [
        {
            "title": item.get("title", ""),
            "owner": item.get("owner", ""),
            "url": item.get("url", ""),
        }
        for item in payload.get("results", [])
        if item.get("url")
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--county", help="county name to search for a parcel service")
    parser.add_argument(
        "--search-parcels",
        action="store_true",
        help="search ArcGIS Online for the county's parcel service",
    )
    parser.add_argument("--layer", help="probe one layer directly, as URL#index")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of prose")
    args = parser.parse_args(argv)

    results: dict[str, Any] = {}
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        if args.layer:
            url, _, index = args.layer.rpartition("#")
            result = probe_layer(client, url, int(index or 0))
            results["layer"] = result.__dict__
            if not args.json:
                _print_layer(result)
            else:
                print(json.dumps(results, indent=2))
            return 0 if result.ok else 1

        for name, spec in SERVICES.items():
            result = probe_service(client, name, spec["url"])
            results[name] = {**result.__dict__, "needs": spec["needs"]}
            if not args.json:
                _print_service(name, spec["needs"], result)

        if args.search_parcels and args.county:
            found = find_parcel_services(client, f"{args.county} County California")
            results["parcel_candidates"] = found
            if not args.json:
                print(f"\n=== candidate parcel services for {args.county} ===")
                for item in found:
                    print(f"  {item['title']}  ({item['owner']})")
                    print(f"      {item['url']}")
                print(
                    "\n  Probe the most likely one for its field names:\n"
                    "    python -m scripts.probe_gis --layer '<url>#0'"
                )

    if args.json:
        print(json.dumps(results, indent=2, default=str))

    if not args.json:
        print(
            "\nNext: put the confirmed URL and field names into "
            "app/providers/parcels/registry.py, then add the county slug to "
            "VERIFIED_COUNTIES so the provider is actually used."
        )
    return 0


def _print_service(name: str, needs: str, result: ProbeResult) -> None:
    status = "OK" if result.ok else "FAILED"
    print(f"\n=== {name} [{status}] ===")
    print(f"  {result.url}")
    print(f"  need: {needs}")
    if not result.ok:
        print(f"  error: {result.detail}")
        return
    if result.detail:
        print(f"  {result.detail[:160]}")
    for layer in result.layers[:25]:
        print(f"    [{layer['id']:>3}] {layer['name']}  ({layer['type']})")
    if len(result.layers) > 25:
        print(f"    ... and {len(result.layers) - 25} more layers")


def _print_layer(result: ProbeResult) -> None:
    print(f"\n=== {result.name} ===")
    print(f"  {result.url}")
    if not result.ok:
        print(f"  error: {result.detail}")
        return
    print(f"  {result.detail}")
    print(f"  {len(result.fields)} fields: {', '.join(result.fields[:40])}")
    print("\n  suggested ArcGisFieldMap:")
    print("    ArcGisFieldMap(")
    for key, value in result.suggestion.items():
        if value:
            print(f'        {key}="{value}",')
        else:
            print(f"        # {key}: no obvious match — check the field list above")
    print("    )")


if __name__ == "__main__":
    sys.exit(main())
