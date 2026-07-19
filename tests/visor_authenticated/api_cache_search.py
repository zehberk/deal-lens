"""Run the small, repeatable Visor API cache probe used during development."""

import argparse
import json
import sys

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from visor_api import VisorClient, VisorListingQuery, cached_listing_search
from visor_scraper.config import get_visor_api_key


CACHE_DIR = PROJECT_ROOT / "cache" / "visor-api-test"
MAX_LISTINGS = 10
QUERY = VisorListingQuery.from_options({
	"make": "Honda",
	"model": "Civic",
	"trim": ("LX", "Sport"),
	"year": 2020,
	"miles_min": 10_000,
	"miles_max": 80_000,
	"sort": "lowest price",
})


def main() -> None:
	parser = argparse.ArgumentParser(
		description="Fetch or reuse the hardcoded Honda Civic API test cache."
	)
	parser.add_argument(
		"--force",
		action="store_true",
		help="Bypass and replace an existing cache entry.",
	)
	args = parser.parse_args()

	client = VisorClient(get_visor_api_key(env_file=PROJECT_ROOT / "api.env"))
	result = cached_listing_search(
		client,
		QUERY,
		cache_dir=CACHE_DIR,
		max_listings=MAX_LISTINGS,
		force=args.force,
	)
	summary = {
		"cache_used": result.cache_used,
		"cache_path": str(result.cache_path),
		"listing_count": len(result.response.get("data", [])),
		"metadata": result.metadata,
	}
	print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
	main()
