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

Run `deal-lens --help` for the installed command help. The legacy
`visor_scraper` command remains a compatibility alias, but new documentation and
automation should use `deal-lens` or `python -m deal_lens`.

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
parameters are reported as unsupported rather than silently approximated.

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

The client uses a 10-second connection timeout and a 30-second read timeout. It
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
