"""Per-county parcel source configuration.

Adding a county means adding an entry here: the URL of its published parcel
layer and the names of the fields holding the APN and owner.  No code change
is required.

The URLs below are the counties' public GIS endpoints. They must be confirmed
against each county's live service before that county is switched on --
counties reorganise their layers, and a stale URL should surface as "no parcel
source configured" rather than as silently missing parcels.
"""

from __future__ import annotations

import httpx

from app.providers.parcels.arcgis import (
    ArcGisFieldMap,
    ArcGisLayerConfig,
    ArcGisParcelProvider,
)
from app.providers.parcels.base import NullParcelProvider, ParcelProvider

#: county slug -> layer configuration.
COUNTY_LAYERS: dict[str, ArcGisLayerConfig] = {
    # Lassen County publishes parcels through its public ArcGIS service.
    # Verify the layer index and field names against the live service before
    # enabling; see docs/PROVIDERS.md.
    "lassen": ArcGisLayerConfig(
        name="Lassen County GIS parcels",
        url="https://services.arcgis.com/LASSEN/ArcGIS/rest/services/Parcels/FeatureServer/0",
        fields=ArcGisFieldMap(apn="APN", owner="OWNER_NAME", acreage="ACRES",
                              address="SITUS_ADDRESS"),
    ),
}

#: Counties whose configuration has been confirmed against the live service.
#: Everything else falls back to the null provider until it is checked, so an
#: unverified URL can never quietly produce wrong parcels.
VERIFIED_COUNTIES: frozenset[str] = frozenset()


def get_parcel_provider(
    county_slug: str | None,
    *,
    client: httpx.Client | None = None,
    allow_unverified: bool = False,
) -> ParcelProvider:
    """The parcel provider for a county, or a null provider."""
    if not county_slug:
        return NullParcelProvider()
    slug = county_slug.strip().lower()
    config = COUNTY_LAYERS.get(slug)
    if config is None:
        return NullParcelProvider()
    if slug not in VERIFIED_COUNTIES and not allow_unverified:
        return NullParcelProvider()
    return ArcGisParcelProvider(config, client=client)


def configured_counties() -> list[str]:
    return sorted(COUNTY_LAYERS)
