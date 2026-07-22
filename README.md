# visor.vin Web Scraper

A lightweight CLI tool that scrapes car listings from [visor.vin](https://visor.vin) using common filters and saves the results as a JSON file.


## Features

- Filter listings by make, model, year, trim, price, mileage, and more
- Save search results as a structured JSON file
- Optional support for reusable presets
- Minimal and fast — built with Playwright and asyncio


## Setup

1. Clone the repo:
   ```bash
   git clone https://github.com/your-username/visor-vin-scraper.git
   cd visor-vin-scraper
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   .\.venv\Scripts\activate.bat  # For Command Prompt in Windows
   .\.venv\Scripts\Activate.ps1  # For PowerShell in Windows
   source .\.venv/bin/activate   # For Git Bash or WSL (Linux/macOS shells)
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Configure Visor API access:

   Copy `api.env.example` to `api.env`, then replace the placeholder with your
   Visor API key. Alternatively, set `VISOR_API_KEY` in the process environment.
   A process environment value takes precedence over `api.env`.

### Visor inventory API

Use the authenticated API client for general listing searches, market facets, and
individual listing detail. Filters use the parameter names from the official Visor
API; sequences are encoded as comma-separated values.

```python
from visor_api import VisorClient, adapt_search_response
from visor_scraper.config import get_visor_api_key


client = VisorClient(get_visor_api_key())
filters = {
	"make": "Toyota",
	"model": "Camry",
	"year": [2024, 2025],
	"inventory_type": "used",
}

listings = client.filter_listings({**filters, "limit": 50})
market = client.filter_facets({
	**filters,
	"facets": "price,miles,days_on_market",
})
listing = client.get_listing(
	listings["data"][0]["id"],
	{"include": "price_history,options"},
)

# Convert API records to the existing DealLens metadata/listings contract.
payload = adapt_search_response(
	listings,
	details={listing["data"]["id"]: listing["data"]},
	request_filters=filters,
)
```

Typed variants are available as `filter_listings_model`,
`filter_all_listings_model`, `filter_facets_model`, and `get_listing_model`.
They validate API and cached payloads into the models exported by `visor_api`,
while the original methods continue returning raw dictionaries for compatibility.
Unknown response fields are preserved during model serialization so a newer API
field is not silently discarded.

The adapter prefers non-null detail values, retains every untouched API record in
`source_data`, and records source paths, calculations, and unavailable reasons in
`provenance`. Missing and incompatible analysis fields are recorded on each
listing and aggregated in `metadata.warnings`; warnings identify the received type
without copying the raw incompatible value. Facet responses can be supplied through
`facets_response`; aggregate market values are kept in metadata and a separate
`facet_result`, never copied onto individual listings.

Cached listing searches request model, trim, and days-on-market facets for the
overall filtered market. When a search explicitly selects trims, DealLens also
requests days-on-market facets separately for each selected trim and preserves the
individual queries and responses alongside the overall market result.

Facet-native Level 1 reports treat user-provided trims as market restrictions.
Every active and recently sold query applies the selected trims, so the report
contains only those trim buckets. Omit the trim filter to discover and compare all
trims returned for the model market.

Level 2 uses an enriched `/v1/listings` search followed by standard listing-detail
requests. The resulting API records are adapted into the legacy analysis-facing
listing shape, cached locally, saved under `output/raw`, and passed to the existing
KBB, dealer-document, CARFAX, scoring, and PDF workflow. New, used, and certified
inventory are included unless the search URL specifies conditions. Use `--force` to
bypass the Level 2 API cache.

The Level 2 report accounts for every returned listing. A complete rating still
requires compatible KBB pricing and a saved vehicle-history report. Listings without
a report are shown separately with a price assessment and an unavailable risk/final
rating; listings without usable pricing show their evaluation failure reason.
The saved DealLens metadata also records the logical `/v1/listings` query and every
overall or per-trim `/v1/facets` query with the UTC time its response was retrieved.
Cache hits retain the original retrieval times.

The client sends the configured key only in the `Authorization: Bearer` header,
uses a 10-second connection timeout and a 30-second response/read timeout, and
retries rate-limit and temporary platform responses a bounded number of times.
`VisorAPIError` exposes Visor's error type and code, HTTP status, parsed response
body, and `Retry-After` header. Connection and response/read failures raise
`VisorConnectionTimeoutError` and `VisorReadTimeoutError`, respectively. Final API
errors are logged once with their type, status, code, and operator-facing message;
credentials and full response payloads are not logged. Retry attempts are logged at
`WARNING`, final failures and timeouts at `ERROR`, and sanitized request timing and
rate-limit telemetry at `DEBUG`.

5. Install browser dependencies for Playwright:
   ```bash
   playwright install
   ```

6. Authentication Setup
   
   This script can be run without cookies, but you will not be able to see any of the features that a subscription can give you (installed options, additional documents, etc.). As of right now, cookie automation is not available; however, there is a simple workaround.

   To get your cookies imported easily, you can install a browser extension called EditThisCookie, navigate to visor.vin, open the extension and click Export. This will copy all your cookies to the clipboard.

   Once that is done, create a file called cookies.json and place it in the .session folder.

   ***Warning:*** If you run this script without authentication, it will run for considerably longer!


## Running the Scraper

You must specify either:

- `--make` and `--model` (required), or
- `--preset` with both values defined

### Basic Usage

```bash
python -m scraper --make "Jeep" --model "Wrangler" --trim "Rubicon" --year "2023 2024" --sort "Newest"
```

### Using a Preset

```bash
python -m scraper --preset "default"
```

Presets should be defined in `presets/presets.json`. See [presets.docs.md](presets/presets.docs.md) for the format and allowed values.

### Help

Use `--help` for a more thorough list of arguments

```bash
python -m scraper --help
```


## Output

Results are saved to a `.json` file in the root directory, with the filename based on your query (e.g., `Jeep_Wrangler_listings_{timestamp}.json`).

Progress and summary info are shown in the terminal. See [output.docs.md](output/output.docs.md)

The field comparison, compatibility decision, unavailable-value policy, and
sanitized contract fixtures for the migration from web scraping to the official
Visor API are documented in [docs/visor-api-migration.md](docs/visor-api-migration.md).


## Testing

To run all tests:

```bash
pytest
```


## License

This project is licensed under the [MIT License](https://opensource.org/licenses/MIT). You are free to use, modify, and distribute it with attribution.
