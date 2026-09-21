# Data source licensing for a commercial deployment

This tracker is intended to be sold, which makes the licence on each upstream
data source a commercial question rather than an academic one. This is a
summary of what each source allows, why the defaults are what they are, and
where the real risk sits.

**This is not legal advice, and the terms below could not be fetched and
re-read from the build environment** (its network policy blocks outbound
hosts). Confirm each one before launch — particularly the county parcel
agreements, which are the only genuinely risky item in the stack.

## The records themselves

Pesticide use reports, restricted-materials permits, notices of intent,
notices of proposed action and investigation reports are **California public
records**, obtained under the Public Records Act. Public records carry no
copyright, and the facts in them — who applied what, where, when — are not
copyrightable in any case. Republishing them, including commercially, is the
ordinary use of a public record.

What *is* protectable is the work done on top: the grouping of scattered use
reports into applications, the resolved entities, the corrections, the
enrichment and the presentation. That is the product.

## Geospatial and reference data

| Source | Licence | Safe to resell? |
| --- | --- | --- |
| **USDA NAIP imagery** | US government work — public domain | **Yes**, without restriction |
| **USGS National Map imagery service** | Public domain imagery | **Yes** — but it is a shared public service, so self-host tiles at volume |
| **BLM PLSS (CadNSDI)** | US government work — public domain | **Yes** |
| **CAL FIRE FRAP forest practice** | California state open data | **Yes**, with attribution |
| **US Census Geocoder** | US government work — public domain | **Yes**, and no API key |
| **County assessor parcels** | **Varies by county** | **Check each one** — see below |
| Esri World Imagery | Esri licence, tied to an ArcGIS entitlement | **No** — not for an independent commercial product |
| Mapbox Satellite | Commercial, per map load | Yes on a paid plan, but tiles may not be cached or redistributed |
| Google Maps/Earth imagery | Restrictive commercial terms | **No** for this use |
| Nominatim / OpenStreetMap | ODbL | **Risky** — see below |

## Why NAIP rather than Esri or Mapbox

NAIP is flown by the USDA Farm Service Agency, covers California at roughly
60cm, is reflown every two to three years, and is a US government work in the
public domain. For a product that will be sold, that combination is worth more
than slightly prettier imagery:

* no per-load billing that scales with the product's success;
* no prohibition on caching, so tiles can be self-hosted and served fast;
* no entitlement to lose if a vendor changes its terms;
* it is genuinely *aerial* and leaf-on, which matters for seeing treated
  forest stands rather than a smoothed global mosaic.

Esri's World Imagery basemap is free to *use* inside Esri's ecosystem, not to
build a competing commercial product on. Mapbox is usable commercially but
bills per map load and forbids caching, which makes the cost of a busy public
site unpredictable.

## Why the Census geocoder rather than Nominatim

This is the change that most directly follows from selling the product.

OpenStreetMap data is licensed under the **ODbL**, which has a share-alike
provision: producing and publishing a *derived database* can oblige you to
license that database under the ODbL too. A commercial tracker whose value is
its proprietary database does not want to be arguing about whether geocoding
addresses against OSM made its database derivative.

The **US Census Geocoder** avoids the question entirely. It is a US government
work, public domain, needs no API key, has no rate-limit contract to breach,
and covers exactly the addresses this product cares about. It is less tolerant
of loosely-formatted input than Nominatim, which is the trade, and the tracker
falls back gracefully when an address will not resolve.

Nominatim remains available by configuration for a non-commercial deployment.

## County parcel data — the real risk

Parcels are the one place where a county may assert rights or charge for
redistribution. California counties differ sharply:

* many publish parcels as open data through an ArcGIS service, with no
  restriction beyond attribution;
* some require a signed data-licence agreement for bulk use or redistribution;
* a few sell parcel data and prohibit republishing the geometry.

The tracker is built so this is a per-county decision rather than a
system-wide gamble. `app/providers/parcels/registry.py` holds one entry per
county, and a county is only used once its slug is added to
`VERIFIED_COUNTIES`. Before adding a county, confirm:

1. the terms attached to its published parcel service;
2. whether redistribution of geometry is permitted, or only of the APN and
   owner name;
3. whether attribution is required, and in what form.

Where redistribution of geometry is not permitted, the tracker can still work:
it can match parcels for internal resolution and publish the APN, the owner
and the reported acreage without drawing the polygon. That capability is worth
keeping in mind rather than treating an awkward county as a blocker.

## Attribution

Sources requiring attribution should be credited in the map's attribution
control and on the About page. The front end reads the basemap credit from
`NEXT_PUBLIC_BASEMAP_ATTRIBUTION` so it is deployment-specific rather than
hard-coded.

## Open questions to settle before launch

* Terms for each county parcel service the product ships with.
* Whether CAL FIRE attribution is required in a specific form.
* The licence the tracker itself is released under, if any — currently
  undecided, and it should be settled before a public deployment.
