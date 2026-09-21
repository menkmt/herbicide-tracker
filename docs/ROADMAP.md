# Status against the build plan

Honest accounting of what is built, what is partial, and what is not started.
"Verified" means exercised against the real Lassen County documents.

## Built and verified

| Area | State |
| --- | --- |
| PUR site-ID decoding | All known formats, with site-ID/MTRS cross-validation and OCR repair. Verified on every site ID in the sample data. |
| Extraction: tabular exports | Verified — a 210-row Lassen export becomes 84 records, every field mapped. |
| Extraction: use-report forms | Verified, including a non-PLSS site ID falling back to the form's own section boxes. |
| Extraction: restricted-materials permits | Verified on two .docx permits and a 16-page scan (157 of 159 sites recovered from the scan). |
| Extraction: NOPAs | Verified on a real 13-page scanned NOPA. |
| Extraction: investigation reports | Verified on a real report — parcel, lab results, conclusion, licensees, findings. |
| OCR pipeline | Verified. Blank pages are detected and skipped rather than OCR'd into plausible garbage. |
| Format auto-detection | Verified across all five document types. |
| Clustering into applications | Verified — 84 records to 20 projects, with proximity gating and a different-operator disqualifier. |
| Chemical flags | Verified — restricted status cited to the county permit; watchlist kept separate. |
| Adjuvant and dye identification | Verified on the sample data. |
| Units and totals | Verified, including refusal to add gallons to pounds. |
| Aggregation | Verified — by county, year, operator, landowner, applicator, product, township, method. |
| Database and migrations | 31 tables on PostGIS 3.4, clean migration round trip. |
| Import pipeline | Verified end to end, idempotent by SHA-256. |
| Public API | Verified against imported data, including filtering by active ingredient. |
| Admin dashboard | Verified — drag, import, review queue, publish. |
| Public front end | Verified — grid, application pages, chemical pages, colour key. |
| WordPress plugin | Written and lint-clean on PHP 8.4; not yet run inside a live WordPress install. |
| Access tiers and rate limiting | Verified — aggregates return 403 with an upgrade path for anonymous callers. |
| CPRA sync logic | Verified against a fake Inquisitor client; built from Inquisitor's real API. |

## Built but not verifiable in this environment

The network policy here blocks outbound hosts, so nothing below could be run
against a live service. Request construction, paging, parsing and all decision
logic are covered by tests using a mocked transport; the service URLs and field
names need confirming before each is switched on.

| Area | What remains |
| --- | --- |
| County parcel providers | Confirm Lassen's ArcGIS layer URL and field names, then add it to `VERIFIED_COUNTIES`. Until then it returns nothing rather than wrong parcels. |
| PLSS section geometry | Confirm the BLM CadNSDI layer index and attribute names. |
| CAL FIRE forestry lookup | Confirm the Forest Practice service's layer indices and attribute names. |
| Geocoding | Defaults to the US Census Geocoder (public domain, keyless). Request and response handling are covered by tests against a mocked transport; the live endpoint could not be called from here. |
| Business enrichment | Needs a search provider and key; disabled by default. |
| Inquisitor connection | Needs the workstation URL, workspace ID and an actor mapped in Inquisitor's trusted-proxy authenticator. |

## Not started

| Area | Notes |
| --- | --- |
| Map generation (static thumbnails) | The interactive map is built; server-rendered static images for social cards are not. |
| Person and company profile pages | API endpoints exist; the public pages and the enforcement blocks on them are not built. |
| Enforcement persistence | The extractors and model are complete; the database tables and pipeline stage that store them are not. |
| DPR product database resolver | The interface and the seed file exist; the live DPR provider does not. |
| Chemical page content | The structure is there; the sourced narrative sections are deliberately empty until they can cite agency findings. |
| Notice-of-intent matching | NOIs are modelled and stored; matching an NOI to the use report that later fulfils it is not implemented. |
| Redis job queue | Imports run synchronously. Fine for the current volume; a queue is needed before large multi-county batches. |
| Purchase tracking | Volumes bought by counties, cities and the state — planned, not started. |
| Subscriber billing | The tiering exists; there is no payment integration. |

## Known data findings

Real problems the tracker surfaced in the sample documents, left as review items
rather than resolved by guesswork:

* Three of 278 permitted sites in the 2020 Beaty permit carry a site ID that
  disagrees with its own printed MTRS.
* One reported Velpar application works out at roughly 351 lb/acre, which is
  implausible and suggests a transcription or unit error in the source.
* All eleven products in the sample resolve only to seed data, so no
  active-ingredient totals are published as fact yet.
