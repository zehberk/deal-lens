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
- the page's comparison-specific average days on market as an exact Visor value
- the page's seven-day sell chance as a direct API fact; DealLens may calculate and
  disclose its own estimate as described below
- price-history mileage and legacy `lowest` marker when absent from API entries
- arbitrary labels from the scraped specification table
- scraper warnings/errors and positional listing number

The API does provide `days_on_market`, facet-level days-on-market statistics, a
generic `vhr_url`, structured options, and build verification. These are related but
are not interchangeable with the missing legacy fields.

## Level 2 acquisition decision

Level 2 should use one enriched listing search followed by standard vehicle-detail
requests for the returned listings:

1. Call `GET /v1/listings` with `include=options,price_history`, the required
   listing projection, and a maximum page size of 100.
2. Call `GET /v1/listings/{listing_id}` without expansions for each returned
   listing.

Do not set `inventory_type` unless the user selects one or more conditions. The
default Level 2 search therefore includes new, used, and certified vehicles. Sorting
can cause the first 100 results to contain only some of those conditions.

This combination maps the usable legacy `scraper.py` listing fields:

- stable ID and VIN;
- year, make, model, trim, version, condition, price, and mileage;
- listing age, dealer listing URL, and photos;
- seller name, location, stock number, and phone when available;
- normalized vehicle specifications;
- installed options and price history.

CARFAX, AutoCheck, and window-sticker URL discovery is not part of this Visor API
acquisition decision. It remains supplemental dealer-site enrichment. The material
legacy loss is Visor warranty status and coverage limits, which are not available
from the Public API and are no longer reliably accessible through the paywalled web
view. Record warranty evidence as unavailable unless a separate approved source
provides it.

### Cost

The API's usage headers report these request classes and prices:

| Request | Usage class | Cost |
| --- | --- | ---: |
| Enriched listing search, up to 100 returned listings | `listing_search_enriched` | $0.04 per request |
| Standard listing detail | `vehicle_detail` | $0.003 per listing |

A full 100-listing run therefore costs `$0.04 + (100 * $0.003) = $0.34`.
Options and price history should not also be requested on every detail call because
the enriched search already supplies them.

### Live field audit

An authenticated audit on 2026-07-21 requested 100 active 2024-2026 Subaru
Crosstreks with at most 75,000 miles, sorted by price ascending. The search matched
25,365 records and returned 100: 91 used and 9 certified. New inventory was not
excluded; no new vehicle ranked among the lowest-priced 100 at collection time.

All 100 search rows contained the core identity, price, mileage, condition, listing,
dealer, photo, feature, option, and MSRP fields requested by the current projection.
Decoded options were non-empty for 99, and price history was non-empty for 62.
Standard detail succeeded for 89 before the account's rate limit exhausted bounded
retries. For those 89 records, normalized build data was complete; dealer phone was
available for 85, generic `vhr_url` for 21, and detailed provider pricing for 56.
These counts describe one live audit and must not become fixture expectations.

The audit also confirmed that standard detail returns `options` and `price_history`
as unavailable when those expansions are omitted. The enriched search is therefore
the least expensive place to obtain both for all listings.

### Fields collected but not currently used by Level 2

Preserve these fields in raw source data and provenance, but do not add them to
Level 2 calculations merely because they are available:

- MSRP and discount from MSRP;
- normalized features and option-package codes;
- installed-option values and price history;
- provider pricing totals and line items;
- generic VHR URL;
- dealer phone and coordinates;
- inventory, sold, and last-checked dates;
- listing lifecycle and availability statuses;
- assembly location and window-sticker verification;
- base and combined MSRP;
- colors, cylinders, doors, and seating capacity.

### Rate limiting

The current account tier advertises 10 requests per 10 seconds. A live batch with
five concurrent detail workers still produced `429 rate_limit_exceeded` responses,
so Level 2 must not assume that nominal throughput is continuously available. The
shared API client already honors the response's `Retry-After` header and performs
bounded retries. The Level 2 collection service should additionally serialize or
centrally rate-limit detail requests so concurrent workers do not wake and retry as
another burst.

### Implemented collection boundary

`visor_api.level2_service.collect_level2_listings` implements this acquisition
plan. It delegates offset pagination to `VisorClient.filter_all_listings`, then
retrieves standard detail sequentially for each unique stable listing ID. Search
rows remain usable when detail fails; the record stores the sanitized detail error,
an explicit warning, and unavailable provenance. Malformed rows, missing IDs, and
duplicate IDs are retained as structured exclusions rather than disappearing.

`visor_api.level2_cache.cached_level2_collection` stores the complete collection,
including raw search/detail records and exclusions, in an atomic local daily cache.
It supports forced refresh and invalidates corrupt, stale, or incompatible cache
envelopes. The `--level2` CLI workflow now routes through this service, writes the
adapted listings to the existing `output/raw` envelope, and passes those listings to
the legacy Level 2 analysis and report pipeline. Level 1 remains on its separate
facet-native path.

An end-to-end live test on 2026-07-21 collected 10 used 2024 Subaru Foresters,
retrieved all 10 detail records, discovered three dealer history-report links,
completed KBB matching, parsed three saved CARFAX reports, and generated the Level 2
PDF. The test also established that API-null document URLs must use the same URL
validity check as legacy `"Unavailable"` sentinels.

## Market-overview source determination

The current active-inventory facet response and a sold-inventory experiment establish
the following source boundaries:

| Market value | API source | Determination |
| --- | --- | --- |
| Total for sale | Active `/v1/facets` `data.total` | Direct source fact. |
| Current days on market | Active `/v1/facets` `stats.days_on_market` | Direct aggregate for listings still in the filtered market. Keep count and missing count with the statistic. |
| Sold in 14 days | `/v1/facets` with the same market filters plus `sold_within_days=14`; read `data.total` | Direct aggregate. Do not infer the window from individual `sold_date` values. |
| Time to sale for recent sales | The same sold facet response's `stats.days_on_market` | Direct aggregate for the sold cohort; it is not interchangeable with active days on market. |
| Market days supply | Active facet total and sold-within-14-days facet total | Calculated value requiring both cohorts. Record the formula and inputs; leave unavailable when the sales rate is zero. |
| Historical market snapshot | Listing or facet request with `snapshot_date` | Separate historical cohort. Never combine it with current inventory without an explicitly labeled comparison. |
| Seven-day sell chance | Listing `days_on_market` plus recent sold-cohort `stats.days_on_market` | Optional DealLens urgency estimate using a fitted Weibull distribution; never present it as a Visor API fact. |

The authenticated experiment used identical year, make, and model filters for
`/v1/listings` and `/v1/facets`, plus `sold_within_days=14`. Both endpoints reported
378 matching records. Sample records had `status` and `inventory_status` set to
`sold` while `sold_date` was null, confirming that the server-side rolling filter is
the authoritative window for this workflow. Counts are time-dependent; 378 is test
evidence, not a stable market value or fixture expectation.

### Optional seven-day urgency estimate

Visor's user-interface explanation describes its demand percentage as a comparison
between the listing's current time on the lot and the distribution of how long
similar vehicles took to sell. It identifies a Weibull distribution fitted from the
sold cohort's mean and standard deviation, with the Gamma function evaluated using
a Lanczos approximation. This description makes a comparable DealLens estimate
possible, but it does not establish Visor's exact cohort selection, conditioning,
rounding, or sparse-data rules.

For a sold cohort with mean days to sale `mu` and standard deviation `sigma`, solve
the Weibull shape `k` numerically from:

```text
(sigma / mu)^2 = Gamma(1 + 2/k) / Gamma(1 + 1/k)^2 - 1
```

Then calculate the scale `lambda`:

```text
lambda = mu / Gamma(1 + 1/k)
```

For a listing already active for `t` days, the proposed conditional probability of
selling within the next `w` days is:

```text
1 - exp(-(((t + w) / lambda)^k - (t / lambda)^k))
```

Use `w = 7` for the user-facing urgency indicator. Python's standard-library
`math.gamma` is sufficient; DealLens does not need its own Lanczos implementation.

This value is optional and must be labeled as an estimate. It is intended only as a
user-urgency signal, not as a verified vehicle fact, deterministic deal-rating
input, or prediction that a particular vehicle will sell. Preserve these inputs and
decisions with every calculated value:

- the exact sold-cohort market filters and `sold_within_days` window;
- facet retrieval timestamp;
- sold sample `count` and `missing` count;
- sold days-on-market mean and standard deviation;
- listing days on market and forecast window;
- fitted Weibull shape and scale;
- calculation version and an explicit `kind: estimate` designation.

Return the estimate as unavailable rather than inventing a value when the cohort is
below a documented minimum sample size, the mean is not positive, the standard
deviation is not positive, the fit has no stable positive solution, required inputs
are missing, or numerical evaluation fails. The minimum sample size and any display
rounding remain product decisions and must be fixed and tested before this metric is
used in a report.

## API query provenance

Saved DealLens output records acquisition provenance under
`metadata.sources.visor_api`. The `listings` entry contains the logical query,
requested listing limit, endpoint, and UTC retrieval time. The `facets.overall`
entry records the overall facet query and retrieval time, while `facets.by_trim`
contains one equivalent entry for every explicitly selected trim.

Retrieval times describe when DealLens received each API response. They are not the
listing's source timestamp and must not be replaced when a cached response is read.
Queries contain normalized URL parameters only; authorization headers and API keys
must never be included. Pagination's requested maximum is recorded separately from
the logical query because the client may issue multiple physical `limit`/`offset`
requests to satisfy it.

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
