# Ground Truth — California pesticide application tracker

Turns the pesticide records that companies are required to file with California
county agricultural commissioners into a public, searchable record of what was
sprayed, where, by whom, and what happened afterwards.

Built for the publisher, designed statewide from the first table, and named
through configuration so it can be deployed and sold under another name.

```
Drop the county's documents in  →  the application does the rest
                                →  a person reviews only the exceptions
                                →  publish
```

## What it does

**Reads whatever the county sends.** Tab-separated use-record exports,
spreadsheets, per-application use-report forms, restricted-materials permits,
notices of proposed action, investigation reports — as CSV, Excel, Word, native
PDF, or a photocopied scan with no text layer at all. File type is detected from
content, so nothing needs sorting first.

**Locates every application.** California PUR site IDs encode a township, range
and section; the decoder handles the packed form (`291223` → T29N R12E §23), the
separator form, explicit legal descriptions, DPR's separate columns, and the
MTRS strings printed on permits. Where a document gives both a site ID and an
MTRS, they are cross-checked against each other — which both corroborates the
decode and repairs the letter-for-digit errors OCR makes on scans.

**Groups reports into applications.** One forestry project is filed as dozens of
separate use reports. The tracker groups them into a single public record — in
the real Lassen data, 84 reports become 20 projects, one of them thirteen site
IDs treated over three days — while keeping every source report intact.

**Identifies the chemicals.** Products resolve to EPA registrations and active
ingredients; surfactants, crop oils and marker dyes are identified as tank
additives and never counted as pesticides. Restricted-material status is cited
to the county permit that establishes it.

**Says where it got everything.** Every published fact carries the document,
page or column it came from, its retrieval date and a confidence level.

**Refreshes itself.** Once a month it asks the Inquisitor public-records system
for anything county agricultural commissioners have produced since the last run
and feeds it through the same pipeline.

## Quick start

```bash
cp .env.example .env          # then edit
docker compose up -d
docker compose exec api alembic upgrade head
```

* Admin dashboard: <http://localhost:8000/admin>
* Public site: <http://localhost:3000>
* API docs: <http://localhost:8000/api/docs>

Without Docker:

```bash
# backend
cd backend && python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload

# front end
cd frontend && npm install && npm run dev
```

OCR needs `tesseract-ocr` and `poppler-utils` on the host. Without them scanned
documents are reported as unreadable rather than silently skipped.

## Repository layout

| Path | What lives there |
| --- | --- |
| `backend/app/core/` | Site-ID decoding, name resolution, units, coverage, confidence, provenance, access tiers |
| `backend/app/extraction/` | One profile per source format, plus content-based detection |
| `backend/app/clustering/` | Grouping use reports into applications |
| `backend/app/chemicals/` | Product resolution, active ingredients, adjuvants, warning flags |
| `backend/app/enforcement/` | NOPAs, violations and corporate relationships |
| `backend/app/providers/` | Parcels, PLSS, CAL FIRE, geocoding, Inquisitor, business enrichment |
| `backend/app/pipeline/` | Import orchestration and storage |
| `backend/app/api/` | Public, geographic and admin APIs |
| `frontend/` | Next.js public site |
| `wordpress-plugin/` | WordPress integration with real, crawlable URLs |
| `docs/` | Architecture, data sources, deployment, roadmap |

## Things this tracker will not do

These are deliberate, and they are why the output can be trusted.

**It does not invent project names.** A project is named from an overlapping
CAL FIRE forest-practice document, or from the property owner, or not at all.

**It does not claim a parcel was sprayed.** A use report gives a square-mile
section and an acreage. Where the operator's parcels in that section can be
identified, the map outlines them — as property associated with the
application, with the reported acreage always shown beside it.

**It does not add up incompatible units.** Gallons and pounds are kept apart,
and an ambiguous "ounce" is resolved from the product's formulation or reported
separately. A total that is missing something says so.

**It does not call a watchlisted chemical restricted.** Regulatory restrictions
and the publisher's editorial watchlist are both shown in red, and every flag
states which of the two it is.

**It does not call an allegation a finding.** A Notice of Proposed Action is an
allegation. A dismissed one is published as dismissed.

**It does not guess when it is unsure.** An ambiguous site code, an owner who
matches no parcel, an unidentifiable product — each becomes a review item with
a reason a person can act on.

## Scope

* **From 2020 onward**, with no end date.
* **Forestry and timberland**, by DPR site code 30000. Rights-of-way,
  invasive-plant and waterway treatments are already recognised and stored but
  are not published until the forestry side is complete; enabling them is a
  configuration change, not a re-import.

## Tests

```bash
cd backend && .venv/bin/python -m pytest -q          # 236 tests
TRACKER_TEST_DATABASE_URL=postgresql+psycopg://... .venv/bin/python -m pytest -q   # adds PostGIS integration
```

Provider tests run against a mocked HTTP transport, so query construction,
paging, geometry translation and error handling are covered without network
access.

## Licence

To be decided before any public deployment.
