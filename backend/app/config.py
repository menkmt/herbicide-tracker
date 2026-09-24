"""Runtime configuration.

Everything environment-specific lives here and arrives through environment
variables, so no credential is ever committed.  Defaults are chosen so a
developer can run the stack locally without configuring anything, while every
production-only concern (real object storage, the Inquisitor connection, API
keys) fails loudly rather than silently degrading.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.coverage import COVERAGE_START


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TRACKER_", env_file=".env", extra="ignore", case_sensitive=False
    )

    environment: str = "development"
    debug: bool = False
    secret_key: str = "dev-only-change-me"

    # --- database -------------------------------------------------------
    database_url: str = "postgresql+psycopg://tracker:tracker@localhost:5432/tracker"
    database_echo: bool = False

    # --- queue and storage ----------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    #: Where immutable uploaded source files live.  Local disk in development;
    #: an S3-compatible bucket in production.
    storage_backend: str = "local"
    storage_local_path: str = "./storage"
    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None

    # --- coverage --------------------------------------------------------
    coverage_start: date = COVERAGE_START

    # --- public site -----------------------------------------------------
    public_base_url: str = "https://example.org"
    api_base_url: str = "http://localhost:8000"
    #: Counties whose data is published.  Empty means all imported counties.
    published_counties: list[str] = Field(default_factory=list)

    # --- access control --------------------------------------------------
    #: Requests without an API key get this tier.
    anonymous_tier: str = "public"
    #: Per-minute request budget for anonymous callers.
    anonymous_rate_per_minute: int = 60
    #: Per-minute budget for an authenticated commercial key.
    subscriber_rate_per_minute: int = 600
    #: Largest page an anonymous caller may request.  Bulk access is a paid
    #: tier, so the public page size is deliberately modest.
    anonymous_max_page_size: int = 50
    subscriber_max_page_size: int = 500
    admin_token: str | None = None

    # --- external services ----------------------------------------------
    #: "census" (public domain, keyless, the default for a commercial
    #: deployment) or "nominatim" (ODbL — see docs/LICENSING.md).
    geocoder_provider: str = "census"
    #: Left empty so each provider uses its own documented endpoint.
    geocoder_url: str = ""
    geocoder_api_key: str | None = None
    #: A contact address is required by Nominatim's usage policy.
    geocoder_user_agent: str = "HerbicideTrackerCA/0.1 (info@example.org)"

    calfire_fp_gis_url: str = (
        "https://egis.fire.ca.gov/arcgis/rest/services/FRAP/ForestPractice/MapServer"
    )
    caltrees_search_url: str = "https://caltreesplans.resources.ca.gov"
    plss_service_url: str = (
        "https://gis.blm.gov/arcgis/rest/services/Cadastral/BLM_Natl_PLSS_CadNSDI/MapServer"
    )

    dpr_product_url: str = "https://www.cdpr.ca.gov/docs/label/labelque.htm"
    dpr_license_url: str = "https://calpip.cdpr.ca.gov"

    #: Basemap tiles for generated static maps and the interactive map.
    basemap_style_url: str = ""
    basemap_tile_url: str = ""
    basemap_attribution: str = ""

    # --- web enrichment ---------------------------------------------------
    #: Search backend used to find public business contact details.  Disabled
    #: until a key is configured; the tracker never scrapes without one.
    web_search_provider: str = "disabled"
    web_search_api_key: str | None = None
    web_search_endpoint: str | None = None
    #: Enriched contact details always land in review before publication.
    enrichment_requires_review: bool = True

    # --- Inquisitor (CPRA) -----------------------------------------------
    inquisitor_enabled: bool = False
    inquisitor_base_url: str = ""
    inquisitor_workspace_id: str = ""
    inquisitor_actor: str = ""
    inquisitor_open_campaign: bool = True
    #: Sending records requests to agencies stays a human decision by default.
    inquisitor_auto_send: bool = False
    #: Day of the month the sync runs.
    inquisitor_sync_day: int = 1

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    def require_production_secrets(self) -> list[str]:
        """Names of settings that must be supplied in production."""
        missing: list[str] = []
        if self.is_production:
            if self.secret_key == "dev-only-change-me":
                missing.append("TRACKER_SECRET_KEY")
            if self.storage_backend == "s3" and not self.s3_bucket:
                missing.append("TRACKER_S3_BUCKET")
            if not self.admin_token:
                missing.append("TRACKER_ADMIN_TOKEN")
            if self.inquisitor_enabled and not self.inquisitor_base_url:
                missing.append("TRACKER_INQUISITOR_BASE_URL")
        return missing


@lru_cache
def get_settings() -> Settings:
    return Settings()
