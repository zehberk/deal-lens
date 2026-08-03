# DealLens

DealLens creates data-driven vehicle-shopping reports from Visor inventory data,
KBB valuations, saved vehicle-history reports, and other explicitly identified
sources. Visor's official Public API is the listing-data source.

## Analysis levels

DealLens supports three workflows:

1. **Market overview:** summarizes a defined make, model, year range, trim set,
   condition, and market area using Visor facets and KBB comparisons.
2. **Listing evaluation:** evaluates every eligible listing with deterministic
   scoring, explicit evidence, uncertainty, and color thresholds.
3. **Negotiation preparation:** prepares leverage points and questions for one
   listing without claiming that a seller will accept a particular price.

Level 1 uses aggregate Visor facet responses. Level 2 uses a paginated enriched
listing search followed by standard listing-detail requests. Level 3 uses the
listing API cache and the current negotiation-analysis workflow.

## Architecture

The main boundaries are:

- `visor_api/`: authentication, requests, pagination, typed API models, caching,
  query translation, and adapters into stable DealLens records;
- `analysis/`: deterministic normalization, market calculations, scoring, and
  report preparation;
- `templates/`: Level 1, Level 2, and Level 3 report presentation;
- `deal_lens/`: the primary CLI, CLI support, and application configuration;
- `tests/unit/`: offline tests using fakes and recorded API fixtures; and
- `tests/visor_authenticated/`: manual, explicitly opted-in API probes that may
  incur usage charges.

Listing records cross those boundaries as the source-independent
`deal_lens.models.Listing` domain model. Visor response classes remain transport
models, while pricing, ratings, risk, and narrative stay in analysis context and
result models. Legacy saved dictionaries are converted at load boundaries and
remain serializable in their established format during migration.

The domain-model package also defines typed compatibility boundaries for KBB
pricing caches, VIN resolutions, vehicle configurations, completed lookups and
national tables; supplementary-resource attempt state; dealer fees, installed
options, price history, warranty coverage, provenance and data warnings; and
persisted listing datasets with metadata. These models preserve unknown legacy
fields so individual subsystems can migrate without invalidating saved files.

Raw API facts remain separate from calculated values and AI-written explanations.
Important report inputs retain source provenance. Missing API fields remain
unavailable rather than being guessed.

## Requirements and setup

DealLens targets Python 3.14.

```powershell
git clone https://github.com/zehberk/deal-lens.git
cd deal-lens
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -e .
playwright install
```

Playwright is still required by KBB and approved supplemental dealer-document
workflows; it is not used to authenticate to Visor or replace the Visor API.
VIN resolution and KBB price gathering run installed Google Chrome in headless
mode with a normal browser context; KBB rejects Playwright's bundled headless
Chromium at its edge layer.
CARFAX requires headed Chrome, so on Windows DealLens starts its browser off-screen,
keeps it enabled without taking focus, and verifies its off-screen position before
downloading reports.
If CARFAX presents an interactive verification puzzle, DealLens restores and focuses
the browser and waits for Enter after the user completes it. DealLens then verifies
that the report loaded, with a five-minute timeout, and parks Chrome off-screen again.
KBB browser navigation is bounded to 30 seconds across retries, while individual
DOM locator waits are bounded to 10 seconds, with up to 30 seconds for KBB's
dynamically rendered price advisor. Missing national fair-purchase prices remain
unavailable. The
KBB page and its embedded price advisor retain the browser context supplied by
KBB; DealLens does not inject or cache a postal code for KBB pricing.

Level 1 retains its model/trim-table pricing workflow. For used and certified
Level 2 and Level 3 listings, DealLens submits an already-collected listing VIN
to KBB to resolve KBB's exact used style (for example, `LUXE Sport Utility 4D`).
It validates that exact style page as a used-vehicle page and only accepts local
FPP/FMV from that VIN-resolved URL. New Level 2 and Level 3 listings also attempt
VIN resolution so body-specific styles can be retained. When KBB does not return
a style for a new VIN, they gracefully fall back to the matching row on KBB's
year/make/model pricing table, retain its MSRP and national FPP, and follow that
row's trim link for local FPP. Compatible later
listings may reuse the resolved configuration; optional body-style, fuel, and
powertrain fields constrain reuse when both records provide them. After resolving
listings, DealLens loads the national trim table and token-matches MSRP and national
FPP to each canonical KBB style. It also retains the matched trim link and its
table-local FPP separately from pricing collected from the VIN-resolved style.
For used vehicles, DealLens selects comparison FPP in the deterministic order
VIN-local, table-local, then national. New vehicles use table-local, then national;
VIN resolution may identify their canonical body style, but a used-price VIN-local
result is never used as their comparison benchmark. Every selected value remains
paired with its source URL and timestamp.
Stale higher-priority values are disclosed and skipped. Legacy cache values with a
recognized VIN/used or new pricing basis migrate to VIN-local or table-local facts;
an unrecognized local value is preserved with uncertain provenance instead of being
silently classified. A failed VIN lookup may still receive national
pricing, but DealLens will not substitute local pricing from a guessed trim or
model-page link. Level 2/3 VIN resolutions, canonical configurations, and national
tables use separate cache namespaces so legacy Level 1 trim rows remain unchanged.
KBB model URL slugs are derived from normalized model names at runtime instead of
being saved once per model year. The pricing cache records explicit, timestamped
lookup completion metadata so successful empty results can be reused until the
normal KBB cache expiration rather than relying on a saved slug as an implicit
completion marker. Legacy `model_slugs` data is removed on the next successful
atomic cache save.
These KBB lookups do not add Visor API requests. National tables, used VIN/style
clusters, and unique new trim local-price pages use separate bounded worker pools.

Each `deal-lens` invocation writes a timestamped DEBUG diagnostic log under
`logs/`. The log records the command arguments and KBB national, trim, and
price-advisor source URLs for later verification. Logging at every severity is
file-only; the interactive console is reserved for Rich progress and final report
paths.

### Visor API key

Create an API key through your Visor account. Then either set it in the process
environment:

```powershell
$env:VISOR_API_KEY = "your-api-key"
```

or copy the ignored local configuration template:

```powershell
Copy-Item api.env.example api.env
```

Replace `YOUR_API_KEY_HERE` in `api.env`. `VISOR_API_KEY` in the process
environment takes precedence over `api.env`. Never commit `api.env`, credentials,
authorization headers, or authenticated response headers.

Outbound Visor calls are evenly paced to stay within 10 requests per rolling 10
seconds and 60 requests per rolling minute by default. Override either limit in
the process environment or `api.env` with `VISOR_REQUESTS_PER_10_SECONDS` and
`VISOR_REQUESTS_PER_MINUTE`.

DealLens fails with a clear configuration error when the key is missing or still
contains the placeholder.

## Running DealLens

Pass a Visor search URL and choose one analysis level:

```powershell
deal-lens --url "https://visor.vin/search/listings?make=Hyundai&model=IONIQ%205&year=2024,2025,2026&price_max=55000&sort=newest" --level1
```

```powershell
deal-lens --url "https://visor.vin/search/listings?make=Hyundai&model=IONIQ%205&year=2024,2025,2026&price_max=55000&sort=newest" --level2 --max_listings 150
```

```powershell
deal-lens --url "https://visor.vin/search/listings?make=Hyundai&model=IONIQ%205&year=2026&sort=newest" --level3 --max_listings 1
```

Useful collection options:

- `--max_listings N`: maximum listings to retrieve, up to 500;
- `--force`: bypass the applicable daily cache; and
- `--save_docs`: download available supplemental listing documents.

Run `deal-lens --help` for the installed command help. You can also invoke the
CLI with `python -m deal_lens`.

Interactive commands use Rich progress displays for API collection, KBB lookups,
dealer-data searches, and supplemental document downloads. Known work shows a
count and ETA; operations whose size is not yet known use a spinner. Redirected
and non-interactive output remains free of animated progress displays. When a
configured Visor rolling-window limit is reached, the active display identifies
the rate-limit wait and its duration before requests resume.
Dealer-page discovery runs before supplementary files and CARFAX reports are
downloaded, allowing a CARFAX link discovered from a dealer page to be used in
the same invocation. Its progress total includes only listings due for polling;
recent cached dealer results are not requested again until their polling window
expires.
Every command prints its total wall-clock runtime when it finishes.

The standalone `level1`, `level2`, and `level3` commands analyze the latest
compatible saved data in `output/raw`; normal acquisition should use `deal-lens`.

## Supported search filters

DealLens translates these search URL parameters into the Visor API contract:

| Purpose | Parameters | Notes |
| --- | --- | --- |
| Vehicle identity | `make`, `model`, `trim`, `year` | Comma-separated values are supported. Level 1 requires make, model, and at least one year. |
| Inventory | `inventory_type` or `car_type` | Supports `new`, `used`, and `certified`; `cpo` maps to `certified`. Omit it to include every inventory type. |
| Price | `min_price`, `max_price` or `price_min`, `price_max` | Whole-dollar bounds. |
| Mileage | `min_mileage`, `max_mileage` or `miles_min`, `miles_max` | Odometer bounds. |
| Geography | `postal_code`, `radius`, `state`, `latitude`, `longitude` | Radius requires a postal code or coordinates. Browser URL geo-origin/radius parameters are also translated. |
| Historical cohorts | `sold_within_days`, `snapshot_date` | These represent separate sold or historical cohorts and must not be mixed with current inventory. |
| Presentation | `sort` | Controls listing order but is excluded from market-cohort identity. |

Named locations that cannot be translated into a postal code and unknown browser
parameters are reported and ignored rather than silently approximated.

### Sort values

The public API accepts `days_on_market`, `listed_at`, `price`, `miles`, `msrp`, and
`discount`, with a leading `-` for reverse order. `distance` requires a geographic
origin. DealLens also translates these friendly names:

| Friendly value | API value |
| --- | --- |
| `newest` | `days_on_market` |
| `oldest` | `-days_on_market` |
| `cheapest` or `lowest price` | `price` |
| `expensive` or `highest price` | `-price` |
| `lowest_miles` or `lowest mileage` | `miles` |
| `highest_miles` or `highest mileage` | `-miles` |

## API usage, pagination, and rate limits

Visor limits listing pages to 100 records. DealLens requests the largest useful
page without over-fetching: a 150-listing request uses pages of 100 and 50, while a
122-listing request uses 100 and 22. Pagination stops at the requested maximum, an
empty/final page, or invalid/non-advancing pagination metadata.

API calls can cost real money. The Level 2 contract observed during migration was
an enriched listing search plus one standard detail request per returned listing;
the recorded example cost was $0.04 per enriched search request and $0.003 per
detail request. Pricing and account limits can change, so treat Visor's current
usage dashboard and response usage headers as authoritative. `--force` can cause
new billable calls because it bypasses the daily cache.

The client enforces the configured 10-second and one-minute rolling request limits.
It uses a 10-second connection timeout and a 30-second read timeout. It
retries HTTP 429 and 503 responses a bounded number of times, honors `Retry-After`
when supplied, and never retries indefinitely. The account observed during the
migration advertised 10 requests per 10 seconds; do not assume that limit applies
to every account or remains unchanged.

Normal pytest runs block live HTTP requests and replay recorded fixtures. A pytest
test that genuinely requires paid Visor access must use the `live_visor` marker and
is skipped unless `--run-live-visor` is supplied. Manual authenticated probes also
require an explicit `--live` flag.

## API and legacy-output differences

DealLens adapts API responses into the existing analysis-facing envelope so saved
legacy data remains readable where practical. Important differences include:

- API listing IDs are stable strings; legacy scraper IDs were positional integers.
- Missing scalar API values use `null`, not `"N/A"`, `"Unavailable"`, or invented
  substitutes.
- The API does not directly provide Visor warranty coverage/status, seller map URLs,
  scraper-enriched dealer fees, or provider-specific CARFAX, AutoCheck, and window-
  sticker URLs.
- A generic vehicle-history URL is not automatically labeled as CARFAX or
  AutoCheck, and build verification is not treated as a window-sticker URL.
- API timestamps are preserved rather than converted into relative text such as
  “Listed 3 days ago.”
- API options and price history preserve their source structure; missing legacy
  price-history mileage or “lowest” flags are not fabricated.
- Level 1 market values come from exact facet cohorts rather than mixed listing-card
  approximations.

The complete mapping, unavailable-value policy, fixture provenance, and migration
decisions are in [docs/visor-api-migration.md](docs/visor-api-migration.md).

## Testing and diagnostics

Run the offline test suite and Python diagnostics with:

```powershell
python -m pytest
.\.venv\Scripts\pyright.exe
```

Pyright uses `standard` mode from `pyrightconfig.json`. Live Visor calls are not
part of the normal test suite.

## Output and generated reports

Acquisition output is written beneath `output/raw`, with report artifacts in the
corresponding output directories. Generated files, credentials, caches, browser
profiles, and private reports are ignored and must not be committed.

## License

DealLens is available under the [MIT License](LICENSE).
