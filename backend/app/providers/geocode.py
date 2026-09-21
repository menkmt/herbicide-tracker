"""Turning a searched address into a coordinate.

The address a member of the public types into "find applications near me" is
their home address.  It is used to build one query and then discarded: it is
never written to the database, and the only thing logged is that a radius
search happened, not where.
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


class NominatimGeocoder(Geocoder):
    """OpenStreetMap's geocoder.

    Nominatim's usage policy requires an identifying User-Agent and limits
    request rates, so this is appropriate for the public site's occasional
    lookups rather than for bulk work.
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
    if settings.geocoder_provider == "nominatim":
        return NominatimGeocoder(settings)
    return DisabledGeocoder()
