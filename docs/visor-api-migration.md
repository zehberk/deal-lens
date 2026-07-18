# Visor API migration context

This document records the contract comparison used to replace Visor web scraping
with the official Public API. It describes the repository at commit `c73d562` and
the Visor Public API responses captured on 2026-07-18.

## Fixture provenance and sanitization

The fixtures in [`docs/fixtures/visor_api`](fixtures/visor_api) were captured from
these endpoints using the ignored local `api.env` credential:

- `GET /v1/listings` (one Toyota Camry, with projected fields and the `options`
  and `price_history` expansions)
- `GET /v1/listings/{listing_id}` with the same expansions
- `GET /v1/facets` for categorical facets, numeric ranges, and statistics
- `GET /v1/listings?bad_filter=value` for the validation-error envelope

The fixtures preserve field names, types, nesting, explicit nulls, and representative
array members. VINs, listing and dealer identifiers, seller identity and contact
data, locations, URLs, timestamps, totals, and observations were replaced with
stable synthetic values. Long image, feature, bucket, and option arrays were reduced
to representative members. No credential or response header is stored.

These are contract fixtures, not market-data fixtures. Their values must not be used
for pricing analysis or assertions about current inventory.

## Schema decision

Preserve the existing DealLens output envelope (`metadata` and `listings`) and the
current analysis-facing listing keys during the API migration. Add an API adapter at
the acquisition boundary and keep the untouched API response available separately
from the adapted records when useful for provenance and debugging.

Do **not** introduce a versioned DealLens schema merely because the source changed.
The analysis code currently consumes the legacy names directly, and an adapter can
provide those names without a breaking saved-data migration. Introduce a schema
version only with a deliberate breaking redesign of the DealLens contract; at that
point, store a top-level integer `schema_version` and provide a legacy reader or
migration layer.

The adapter must not make an API listing ID look like the scraper's positional ID.
Use the API `id` as the stable listing ID and preserve `vin` separately. Existing
saved scraper data remains readable, but its positional IDs are not stable across
runs.

## Field mapping

`API path` refers to either the listing-search row or listing-detail `data` object.
Detail fields are preferred when both endpoints provide a value.

| API path | DealLens / legacy path | Conversion and provenance |
| --- | --- | --- |
| `id` | `id` | Copy the stable API listing ID as a string. Legacy scraper IDs were positional integers. |
| `vin` | `vin` | Copy without inference. |
| `vehicle.build.year` or `year` | `year` | Copy as an integer. |
| `vehicle.build.make` or `make` | metadata `vehicle.make` | Copy; do not infer from title. |
| `vehicle.build.model` or `model` | metadata `vehicle.model` | Copy; do not infer from title. |
| build `year`, `make`, `model`, `trim` | `title` | Deterministically format non-null components for compatibility; record that it is calculated, not API text. |
| `vehicle.build.trim` or `trim` | `trim` | Copy. |
| `vehicle.build.version` or `version` | `specs["Trim Version"]`, normalized `trim_version` | Copy. |
| `price` | `price` | Copy integer/null. Do not format as currency text. |
| `miles` | `mileage` | Rename and copy integer/null. |
| `inventory_type` | `condition` | Map `new` to `New`, `used` to `Used`, and `certified` to `Certified`; unknown values remain unavailable and are recorded. |
| `listed_at` / detail `inventory_date` | `listed` | Preserve an ISO date/time value in the adapter; do not recreate text such as `Listed 3 days ago`. Days-on-market text may be calculated only for presentation with an explicit reference time. |
| `vdp_url` | `listing_url` | Copy as dealer listing URL. |
| `photo_urls` | `images` | Copy list; an absent/null source becomes an empty list only because this is a collection contract. |
| `dealer.name` / `dealer_name` | `seller.name` | Copy. |
| `dealer.city`, `dealer.state`, `dealer.postal_code` | `seller.location` | Deterministically format the available components; keep the individual raw fields in source data. |
| `dealer.phone` | `seller.phone` | Copy from detail. |
| `stock_number` | `seller.stock_number` | Copy. |
| dealer coordinates / search coordinates | no legacy equivalent | Preserve in source data; expose later only when analysis needs geography. |
| `vehicle.build.body_type` | `specs["Body Style"]` | Rename and copy. |
| `vehicle.build.drivetrain` | `specs["Drivetrain"]` | Rename and copy. |
| `vehicle.build.fuel_type` | `specs["Fuel Type"]` | Rename and copy. |
| `vehicle.build.transmission` | `specs["Transmission"]` | Rename and copy. |
| `vehicle.build.engine` | `specs["Engine"]` | Rename and copy. |
| build colors, cylinders, doors, seating, assembly | corresponding `specs` entries | Rename and copy without filling missing values. |
| `vehicle.build.options` or expanded `options` | `installed_addons.items` | Map `name` and `msrp` to legacy `name` and `price`; preserve option `code` in source data. |
| sum of non-null option `msrp` values | `installed_addons.total` | Calculated value; do not use `combined_msrp - base_msrp` as a substitute because the inputs can have different semantics. |
| `price_history` | `price_history` | Copy entries through an explicit entry adapter. Preserve API timestamps and nullable values; do not fabricate legacy `lowest` or mileage fields. |
| `vhr_url` | `additional_docs.carfax_url` only when provider is verified as CARFAX | A generic VHR URL is not necessarily CARFAX or AutoCheck. Otherwise leave both provider-specific fields unavailable. |
| `vehicle.build.window_sticker_verified` | `window_sticker_present` (normalized only) | This verifies build data provenance; it does not supply `additional_docs.window_sticker_url` and must not be converted into one. |
| `status`, `inventory_status`, `sold_date`, `last_checked_at` | no legacy equivalent | Preserve in source data for lifecycle and provenance. |
| `pricing`, `msrp`, `discount_from_msrp` | no legacy equivalent | Preserve as source facts; any DealLens calculations remain separate. |
| `features`, `options_packages`, normalized colors, powertrain | no legacy equivalent | Preserve in source data and expose through stable internal models when analysis requires them. |
| facet `data.total`, `facets`, `range_facets`, `stats` | metadata `site_info` / market overview inputs | Keep as a separate facet result with request filters and capture time; do not attach aggregate values to individual listings. |

### Existing normalized fields

`analysis.normalization.normalize_listing` derives `is_hybrid`, `is_plugin`,
`report_present`, `window_sticker_present`, and `warranty_info_present`. These remain
calculated fields, not raw API fields. Hybrid/plugin status can use API `fuel_type`
and `powertrain_type`, but document the rule and return unknown when neither supports
a conclusion. Presence flags must be false only when absence is actually known;
source unavailability is unknown, not false.

## Legacy fields unavailable from the Public API

The captured search/detail contracts do not provide these scraper fields directly:

- Visor page URL (it can be constructed from the VIN, but is not an API fact)
- human-relative `listed` text
- warranty `overall_status` and coverage limits
- provider-specific AutoCheck, CARFAX, and window-sticker URLs
- seller map URL and scraper-enriched dealer fee list
- scraper market-velocity values: vehicles sold in 14 days, seven-day sell chance,
  and the page's comparison-specific average days on market
- price-history mileage and legacy `lowest` marker when absent from API entries
- arbitrary labels from the scraped specification table
- scraper warnings/errors and positional listing number

The API does provide `days_on_market`, facet-level days-on-market statistics, a
generic `vhr_url`, structured options, and build verification. These are related but
are not interchangeable with the missing legacy fields.

## Unavailable-value policy

1. Keep missing scalar facts as `null`/Python `None`. Do not use `"N/A"`,
   `"Unavailable"`, `"Unknown"`, zero, empty text, or a guessed value.
2. Keep collections as empty lists/objects only when the source contract confirms
   there are no members. If the collection was not requested or could not be fetched,
   retain an unavailable status in provenance rather than presenting it as empty.
3. Distinguish source facts, deterministic calculations, and estimates. Store each
   important value's source path; calculated values also record the rule and inputs.
4. For compatibility fields that cannot represent the distinction, use `null` and
   add a machine-readable reason such as `not_provided_by_api`, `not_requested`,
   `source_error`, or `not_applicable` in adapter provenance. Do not put reason text
   into the value itself.
5. Never translate a generic or related field into a more specific claim. Examples:
   `vhr_url` is not automatically CARFAX, and `window_sticker_verified` is not a
   window-sticker URL.
6. Include every listing returned by the API. If an analysis cannot use one, retain
   it and record the explicit exclusion reason.

## NHTSA safety-data integration

NHTSA safety data is not present in the anonymous Visor listing HTML and is not part
of the Visor Public API contract captured for this migration. Visor's page loads its
safety panel through a private client/server function. DealLens should use the
official NHTSA APIs as a separate supplemental integration rather than depend on
that private web endpoint.

The NHTSA integration should:

- use the listing's verified model year, make, and model to request model-level
  recalls from `GET https://api.nhtsa.gov/recalls/recallsByVehicle`;
- use NHTSA vPIC VIN decoding when vehicle identity needs independent verification;
- preserve the raw NHTSA campaign number, report-received date, component, summary,
  consequence, remedy, manufacturer, and source URL;
- calculate recall count and time since the newest campaign deterministically,
  recording the calculation date and identifying the date as NHTSA's
  report-received date;
- keep raw NHTSA facts separate from calculated age and any DealLens risk scoring;
- define timeouts and explicit errors for unavailable, malformed, and zero-result
  responses, without silently substituting Visor values; and
- cache responses conservatively with retrieval time and request parameters because
  campaign data can change after a listing is acquired.

Model-year/make/model results indicate campaigns that may concern vehicles of that
configuration. They do **not** prove that the particular VIN is affected, that a
repair remains open, or that the vehicle is unsafe. Reports and negotiation material
must retain that distinction and direct users to NHTSA's VIN recall lookup for
vehicle-specific confirmation. The local proof of concept is
[`tests/visor_authenticated/nhtsa_safety.py`](../tests/visor_authenticated/nhtsa_safety.py).

## Follow-up implementation boundary

The API client should own authentication, timeout, pagination, rate-limit handling,
and bounded retries. A separate adapter should combine search summary and optional
detail data into the compatibility schema. Analysis code should receive adapted
models, not API-specific paths. Unit tests should load these fixtures and cover null,
omitted, malformed, and error responses without live credentials.

## References

- [Filter listings](https://api.visor.vin/docs/api-reference/inventory/filter-listings)
- [Get a listing](https://api.visor.vin/docs/api-reference/inventory/get-a-listing)
- [Filter facets](https://api.visor.vin/docs/api-reference/inventory/filter-facets)
- [Errors and retries](https://api.visor.vin/docs/errors-and-retries)
- [NHTSA datasets and APIs](https://www.nhtsa.gov/nhtsa-datasets-and-apis)
- [NHTSA VIN recall lookup](https://www.nhtsa.gov/recalls)
