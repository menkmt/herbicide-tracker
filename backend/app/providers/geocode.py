"""Turning a searched address into a coordinate.

The address a member of the public types into "find applications near me" is
their home address.  It is used to build one query and then discarded: it is
never written to the database, and the only thing logged is that a radius
search happened, not where.

The default geocoder is the **US Census Geocoder**, not Nominatim, and the
reason is licensing rather than quality. OpenStreetMap data is licensed under
the ODbL, whose share-alike provision can oblige the publisher of a *derived
database* to license that database under the ODbL too. This tracker is sold,
and its value is its database, so it should not be geocoding against a source
that raises that question at all. The Census geocoder is a US government work
in the public domain, needs no API key, and covers exactly the US addresses
this product cares about.

Nominatim remains available by configuration for a non-commercial deployment.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from app.config import Settings, get_settings


class GeocodeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Location:
    latitude: float
    longitude: float
    display_name: str | None = None
    source: str = ""


class Geocoder(ABC):
    @abstractmethod
    def geocode(self, address: str) -> Location | None: ...


class CensusGeocoder(Geocoder):
    """The US Census Bureau's address geocoder.

    Public domain, keyless, and strict about address formatting — it wants a
    real street address rather than a place name, and returns nothing rather
    than guessing at a near match. Returning nothing is the right failure for
    this feature: a radius search centred on the wrong house is worse than one
    that asks the person to be more specific.
    """

    #: The Census service versions its address data; "Current" tracks the
    #: latest vintage rather than pinning to a year that will go stale.
    BENCHMARK = "Public_AR_Current"

    def __init__(self, settings: Settings) -> None:
        self._url = (
            settings.geocoder_url
            or "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
        )

    def geocode(self, address: str) -> Location | None:
        params = {
            "address": address,
            "benchmark": self.BENCHMARK,
            "format": "json",
        }
        try:
            response = httpx.get(self._url, params=params, timeout=20.0)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise GeocodeError(str(exc)) from exc
        except ValueError as exc:
            raise GeocodeError("the geocoder returned an unreadable response") from exc

        matches = (payload.get("result") or {}).get("addressMatches") or []
        if not matches:
            return None
        first = matches[0]
        coordinates = first.get("coordinates") or {}
        if "x" not in coordinates or "y" not in coordinates:
            return None
        return Location(
            latitude=float(coordinates["y"]),
            longitude=float(coordinates["x"]),
            display_name=first.get("matchedAddress"),
            source="us_census",
        )


class NominatimGeocoder(Geocoder):
    """OpenStreetMap's geocoder.

    Nominatim's usage policy requires an identifying User-Agent and limits
    request rates. Note also that its data is ODbL-licensed; see the module
    docstring for why that matters to a commercial deployment.
    """

    def __init__(self, settings: Settings) -> None:
        self._url = settings.geocoder_url
        self._headers = {"User-Agent": settings.geocoder_user_agent}

    def geocode(self, address: str) -> Location | None:
        params = {
            "q": address,
            "format": "jsonv2",
            "limit": 1,
            # California only: the tracker has no data outside it, and
            # constraining the search avoids matching a same-named town
            # elsewhere.
            "countrycodes": "us",
            "state": "California",
        }
        try:
            response = httpx.get(
                self._url, params=params, headers=self._headers, timeout=15.0
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise GeocodeError(str(exc)) from exc
        except ValueError as exc:
            raise GeocodeError("the geocoder returned an unreadable response") from exc

        if not payload:
            return None
        first = payload[0]
        return Location(
            latitude=float(first["lat"]),
            longitude=float(first["lon"]),
            display_name=first.get("display_name"),
            source="nominatim",
        )


class DisabledGeocoder(Geocoder):
    def geocode(self, address: str) -> Location | None:
        raise GeocodeError(
            "address search is not configured; set TRACKER_GEOCODER_PROVIDER"
        )


def get_geocoder(settings: Settings | None = None) -> Geocoder:
    settings = settings or get_settings()
    if settings.geocoder_provider == "census":
        return CensusGeocoder(settings)
    if settings.geocoder_provider == "nominatim":
        return NominatimGeocoder(settings)
    return DisabledGeocoder()
