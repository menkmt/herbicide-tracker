"""FastAPI application.

The backend is independent of WordPress: WordPress is one client of this API,
the Next.js front end is another, and a subscriber's own tooling is a third.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.api import admin, geo, public
from app.config import get_settings
from app.core.access import AccessDenied
from app.core.coverage import COVERAGE_START, DocumentKind
from app.extraction.registry import supported_formats

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(
    title="Herbicide Tracker California",
    version="0.1.0",
    description=(
        "California pesticide-use reports, notices of intent and restricted-materials "
        "permits, turned into a public, searchable record of forestry herbicide "
        "applications."
    ),
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.public_base_url, "http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Authorization", "Content-Type"],
)

app.include_router(public.router)
app.include_router(geo.router)
app.include_router(admin.router)


@app.exception_handler(AccessDenied)
async def access_denied_handler(request: Request, exc: AccessDenied) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Headers that cost nothing and remove easy footguns.

    Note these do not stop scraping — nothing served publicly can. They stop
    the API being embedded in someone else's page and being sniffed into
    executing content it did not intend.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    remaining = getattr(request.state, "rate_remaining", None)
    if remaining is not None:
        response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response


ADMIN_UI = Path(__file__).resolve().parent / "admin_ui" / "index.html"


@app.get("/admin", tags=["admin"], include_in_schema=False)
def admin_dashboard() -> FileResponse:
    """The drag-and-drop import dashboard.

    Served as a single static page rather than a second front end: the
    administrator's whole job is drop files, read the summary, resolve the
    exceptions and publish, and a build step would add nothing to that.

    The page itself is unauthenticated because it contains no data — every
    action it performs carries the administrator token, which is held in the
    browser tab and never stored.
    """
    return FileResponse(ADMIN_UI, headers={"X-Robots-Tag": "noindex, nofollow"})


@app.get("/healthz", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/meta", tags=["meta"])
def meta() -> dict:
    """What this tracker covers — shown on the front page and in the docs."""
    return {
        "coverage_start": COVERAGE_START.isoformat(),
        "coverage_end": None,
        "document_kinds": [
            {"kind": kind, "label": DocumentKind.label(kind)} for kind in DocumentKind.ALL
        ],
        "supported_formats": supported_formats(),
        "published_site_categories": ["forestry"],
        "planned_site_categories": ["rights_of_way", "invasive_plant", "aquatic"],
        "notes": [
            "Applications are grouped from the underlying pesticide use reports; "
            "every source record is preserved.",
            "A notice of intent states an intention to apply and is not evidence "
            "that an application took place.",
            "Parcel maps show the property associated with an application, not a "
            "measured treatment footprint.",
        ],
    }


@app.on_event("startup")
def check_configuration() -> None:
    missing = settings.require_production_secrets()
    if missing:
        # Fail loudly rather than run a production site with dev defaults.
        raise RuntimeError(
            "missing required production configuration: " + ", ".join(missing)
        )
    logger.info("tracker API starting in %s mode", settings.environment)
