"""Providers, exercised against a mocked HTTP transport.

The live GIS and search services are not reachable from CI, so these tests
drive the providers with realistic service payloads. What is verified is
everything the tracker is actually responsible for: query construction,
paging, geometry translation, SQL escaping, error handling and the decisions
made on the results.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.providers.enrichment.business import BusinessEnricher, DisabledSearch, SearchResult
from app.providers.enrichment.policy import Disposition, FactKind, classify
from app.providers.forestry import ForestryPlan, choose_project_name
from app.providers.parcels.arcgis import (
    ArcGisFieldMap,
    ArcGisLayerConfig,
    ArcGisParcelProvider,
)
from app.providers.parcels.base import ParcelProviderError
from app.providers.parcels.registry import get_parcel_provider

SQUARE = {
    "type": "Polygon",
    "coordinates": [[[-120.5, 40.5], [-120.5, 40.6], [-120.4, 40.6], [-120.4, 40.5],
                     [-120.5, 40.5]]],
}


def feature(apn: str, owner: str, acres: float = 640.0) -> dict:
    return {
        "type": "Feature",
        "geometry": SQUARE,
        "properties": {"APN": apn, "OWNER": owner, "ACRES": acres,
                       "SITUS_ADDR": "1 Forest Rd"},
    }


def make_provider(handler) -> ArcGisParcelProvider:
    config = ArcGisLayerConfig(
        name="Test County parcels",
        url="https://example.invalid/arcgis/rest/services/Parcels/FeatureServer/0",
        fields=ArcGisFieldMap(),
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ArcGisParcelProvider(config, client=client)


class TestArcGisParcelProvider:
    def test_parcels_in_a_section_are_normalised(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.url.params))
            return httpx.Response(
                200, json={"type": "FeatureCollection",
                           "features": [feature("001-010-001", "W M BEATY & ASSOCIATES INC")]}
            )

        parcels = make_provider(handler).parcels_in_geometry(SQUARE)
        assert len(parcels) == 1
        assert parcels[0].apn == "001-010-001"
        assert parcels[0].acreage == 640.0
        assert parcels[0].source == "Test County parcels"

    def test_the_query_asks_for_wgs84_geojson_and_intersection(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.url.params))
            return httpx.Response(200, json={"features": []})

        make_provider(handler).parcels_in_geometry(SQUARE)
        assert captured["f"] == "geojson"
        assert captured["outSR"] == "4326"
        assert captured["spatialRel"] == "esriSpatialRelIntersects"
        # The filter geometry must be translated into ArcGIS rings.
        assert "rings" in json.loads(captured["geometry"])

    def test_results_are_paged(self):
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            offset = int(request.url.params.get("resultOffset", 0))
            calls.append(offset)
            if offset == 0:
                return httpx.Response(
                    200, json={"features": [feature(f"p{i}", "OWNER") for i in range(500)]}
                )
            return httpx.Response(200, json={"features": [feature("last", "OWNER")]})

        parcels = make_provider(handler).parcels_in_geometry(SQUARE)
        assert calls == [0, 500]
        assert len(parcels) == 501

    def test_an_owner_search_uses_the_most_distinctive_word(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.url.params))
            return httpx.Response(200, json={"features": []})

        make_provider(handler).parcels_by_owner("W.M. BEATY & ASSOCIATES, INC.")
        assert "BEATY" in captured["where"]

    def test_apostrophes_in_owner_names_are_escaped(self):
        """Business names contain apostrophes; an unescaped one breaks the query."""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.url.params))
            return httpx.Response(200, json={"features": []})

        make_provider(handler).parcel_by_apn("O'BRIEN-1")
        assert "O''BRIEN-1" in captured["where"]

    def test_an_arcgis_error_object_is_raised_not_ignored(self):
        """ArcGIS reports failures with HTTP 200 and an error body."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": {"message": "Invalid layer"}})

        with pytest.raises(ParcelProviderError, match="Invalid layer"):
            make_provider(handler).parcels_in_geometry(SQUARE)

    def test_a_transport_failure_is_raised(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("unreachable")

        with pytest.raises(ParcelProviderError):
            make_provider(handler).parcels_in_geometry(SQUARE)

    def test_a_feature_without_an_apn_is_skipped(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"features": [{"geometry": SQUARE, "properties": {"OWNER": "X"}}]}
            )

        assert make_provider(handler).parcels_in_geometry(SQUARE) == []


class TestProviderRegistry:
    def test_an_unconfigured_county_returns_nothing_rather_than_guessing(self):
        provider = get_parcel_provider("nevada")
        assert provider.parcels_by_owner("anyone") == []

    def test_an_unverified_county_is_not_used_by_default(self):
        """A stale URL must surface as a gap, not as missing parcels."""
        assert get_parcel_provider("lassen").name == "no parcel source configured"

    def test_it_can_be_enabled_explicitly(self):
        provider = get_parcel_provider("lassen", allow_unverified=True)
        assert "Lassen" in provider.name


class TestForestryProjectNaming:
    BEATY = "WM BEATY AND ASSOC."

    def test_a_single_matching_plan_names_the_project(self):
        plan = ForestryPlan(
            identifier="2-24-001-LAS", name="Motor Sheep Biomass", kind="THP",
            landowner="W.M. Beaty & Associates, Inc.",
        )
        result = choose_project_name([plan], owner=self.BEATY)
        assert result.name == "Motor Sheep Biomass"
        assert result.confidence == "high"
        assert not result.needs_review

    def test_several_matching_plans_go_to_review_without_a_name(self):
        plans = [
            ForestryPlan(identifier="A", name="One", kind="THP", landowner=self.BEATY),
            ForestryPlan(identifier="B", name="Two", kind="NTMP", landowner=self.BEATY),
        ]
        result = choose_project_name(plans, owner=self.BEATY)
        assert result.name is None
        assert result.review_reason == "multiple_possible_projects"
        # A specific harvest document is offered ahead of a long-lived plan.
        assert result.alternatives[0].identifier == "A"

    def test_a_plan_filed_by_someone_else_does_not_name_the_project(self):
        plan = ForestryPlan(
            identifier="X", name="Other Project", kind="THP",
            landowner="Sierra Pacific Industries",
        )
        result = choose_project_name([plan], owner=self.BEATY)
        assert result.name is None
        assert result.review_reason == "project_owner_mismatch"

    def test_no_overlapping_plan_means_no_name(self):
        result = choose_project_name([], owner=self.BEATY)
        assert result.name is None
        assert not result.needs_review

    def test_a_plan_links_to_its_caltrees_record(self):
        plan = ForestryPlan(identifier="2-24-001-LAS")
        assert "caltreesplans.resources.ca.gov" in plan.caltrees_url


class TestBusinessEnrichment:
    class Search:
        name = "test"

        def search(self, query, limit=5):
            return [
                SearchResult(
                    "Western Helicopter Services",
                    "https://westernhelicopter.com/contact",
                    "Call (503) 538-9469 or email info@westernhelicopter.com",
                ),
                SearchResult(
                    "Western Helicopter - Yelp", "https://www.yelp.com/biz/western",
                    "(503) 555-0000",
                ),
            ]

    def test_it_uses_the_company_site_and_skips_directories(self):
        """A wrong address on a directory listing is a real harm."""
        result = BusinessEnricher(search=self.Search()).enrich_company(
            "WESTERN HELICOPTER SERVICES", license_number="30717"
        )
        urls = [f.value for f in result.facts if f.kind == FactKind.WEBSITE]
        assert urls == ["https://westernhelicopter.com/contact"]
        assert "(503) 555-0000" not in [f.value for f in result.facts]

    def test_the_licence_number_narrows_the_search(self):
        result = BusinessEnricher(search=self.Search()).enrich_company(
            "WESTERN HELICOPTER SERVICES", license_number="30717"
        )
        assert "30717" in result.searched[0]

    def test_web_sourced_facts_are_never_better_than_medium(self):
        result = BusinessEnricher(search=self.Search()).enrich_company(
            "WESTERN HELICOPTER SERVICES"
        )
        assert all(f.provenance.confidence == "medium" for f in result.facts)

    def test_enrichment_is_off_unless_configured(self):
        result = BusinessEnricher(search=DisabledSearch()).enrich_company("Anyone")
        assert result.facts == []
        assert "not configured" in result.errors[0]


class TestPublicationPolicy:
    """Business versus personal, not findable versus unfindable."""

    @pytest.mark.parametrize(
        "kind",
        [
            FactKind.BUSINESS_PHONE,
            FactKind.BUSINESS_ADDRESS,
            FactKind.WEBSITE,
            FactKind.LICENSE_NUMBER,
            FactKind.PARCEL_OWNER_NAME,
            FactKind.PARCEL_SITUS_ADDRESS,
        ],
    )
    def test_business_and_public_record_facts_publish(self, kind):
        assert classify(kind, "value").disposition == Disposition.PUBLIC

    @pytest.mark.parametrize(
        "kind",
        [FactKind.PERSONAL_ADDRESS, FactKind.PERSONAL_PHONE, FactKind.PERSONAL_EMAIL],
    )
    def test_personal_contact_details_never_publish(self, kind):
        assert classify(kind, "value").disposition == Disposition.RESTRICTED

    def test_a_consumer_email_provider_marks_an_address_personal(self):
        decision = classify(FactKind.BUSINESS_EMAIL, "shane.compton@gmail.com")
        assert decision.disposition == Disposition.RESTRICTED

    def test_a_general_business_address_publishes(self):
        assert classify(FactKind.BUSINESS_EMAIL, "info@wmbeaty.com").is_public

    def test_an_individuals_work_address_needs_confirming(self):
        decision = classify(
            FactKind.BUSINESS_EMAIL, "scompton@wmbeaty.com", subject_is_business=False
        )
        assert decision.disposition == Disposition.REVIEW

    def test_social_media_photographs_are_never_used(self):
        decision = classify(
            FactKind.PROFESSIONAL_PHOTO, "https://scontent.fbcdn.net/photo.jpg"
        )
        assert decision.disposition == Disposition.RESTRICTED

    def test_an_individual_landowners_mailing_address_is_withheld(self):
        """For a person it is usually their home address."""
        decision = classify(
            FactKind.OWNER_MAILING_ADDRESS, "123 Any St", subject_is_business=False
        )
        assert decision.disposition == Disposition.RESTRICTED


class TestGeocoders:
    """Geocoding is licence-sensitive: see docs/LICENSING.md."""

    def test_the_default_is_the_public_domain_census_geocoder(self):
        """OSM's ODbL share-alike is a risk for a product that is sold."""
        from app.config import Settings
        from app.providers.geocode import CensusGeocoder, get_geocoder

        assert isinstance(get_geocoder(Settings()), CensusGeocoder)

    def test_nominatim_is_still_available_by_configuration(self):
        from app.config import Settings
        from app.providers.geocode import NominatimGeocoder, get_geocoder

        geocoder = get_geocoder(Settings(geocoder_provider="nominatim"))
        assert isinstance(geocoder, NominatimGeocoder)

    def test_census_geocoder_reads_a_match(self):
        from app.providers.geocode import CensusGeocoder

        payload = {
            "result": {
                "addressMatches": [
                    {
                        "matchedAddress": "175 RUSSELL AVE, SUSANVILLE, CA, 96130",
                        "coordinates": {"x": -120.6530, "y": 40.4163},
                    }
                ]
            }
        }

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["benchmark"] == CensusGeocoder.BENCHMARK
            return httpx.Response(200, json=payload)

        geocoder = CensusGeocoder.__new__(CensusGeocoder)
        geocoder._url = "https://example.invalid/onelineaddress"
        import app.providers.geocode as geocode_module

        original = geocode_module.httpx.get
        client = httpx.Client(transport=httpx.MockTransport(handler))
        geocode_module.httpx.get = lambda url, **kwargs: client.get(url, **kwargs)
        try:
            located = geocoder.geocode("175 Russell Ave, Susanville CA")
        finally:
            geocode_module.httpx.get = original

        assert located is not None
        assert located.latitude == pytest.approx(40.4163)
        assert located.source == "us_census"

    def test_no_match_returns_none_rather_than_a_near_miss(self):
        """A radius search centred on the wrong house is worse than no answer."""
        from app.providers.geocode import CensusGeocoder

        geocoder = CensusGeocoder.__new__(CensusGeocoder)
        geocoder._url = "https://example.invalid/onelineaddress"
        import app.providers.geocode as geocode_module

        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"result": {"addressMatches": []}})
            )
        )
        original = geocode_module.httpx.get
        geocode_module.httpx.get = lambda url, **kwargs: client.get(url, **kwargs)
        try:
            assert geocoder.geocode("nowhere at all") is None
        finally:
            geocode_module.httpx.get = original
