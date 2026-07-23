from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LISTINGS_PATH = PROJECT_ROOT / "output" / "raw"
DOC_PATH = PROJECT_ROOT / "output" / "vehicles"

# URL strings
BASE_URL = "https://visor.vin/search/listings"

SORT_OPTIONS = {
    "Lowest Price": "cheapest",
    "Highest Price": "expensive",
    "Newest": "newest",
    "Oldest": "oldest",
    "Lowest Mileage": "lowest_miles",
    "Highest Mileage": "highest_miles",
}
SORT_VALUES_TO_LABELS = {v: k for k, v in SORT_OPTIONS.items()}

# Cache file paths
LISTINGS_CACHE = Path("cache") / "listings.cache"
