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

4. Install browser dependencies for Playwright:
   ```bash
   playwright install
   ```

5. Authentication Setup
   
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
