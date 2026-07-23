"""Run the small, repeatable Visor API cache probe used during development."""

import argparse
import json
import sys

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from visor_api import (
	VisorClient,
	VisorListingQuery,
	cached_level1_facets,
	cached_listing_search,
)
from visor_scraper.config import get_visor_api_key


CACHE_DIR = PROJECT_ROOT / "cache" / "visor-api-test"
MAX_LISTINGS = 150
QUERY = VisorListingQuery.from_options({
	"make": "Hyundai",
	"model": "IONIQ 5",
	"year": (2024, 2025, 2026),
	"price_max": 55_000,
	"sort": "newest",
})


def main() -> None:
	parser = argparse.ArgumentParser(
		description="Fetch or reuse the hardcoded Hyundai IONIQ 5 API test cache."
	)
	parser.add_argument(
		"--live",
		action="store_true",
		help="Acknowledge that this command may make paid live API requests.",
	)
	parser.add_argument(
		"--force",
		action="store_true",
		help="Bypass and replace an existing cache entry.",
	)
	args = parser.parse_args()
	if not args.live:
		parser.error("live Visor API access requires the explicit --live flag")

	client = VisorClient(get_visor_api_key(env_file=PROJECT_ROOT / "api.env"))
	result = cached_listing_search(
		client,
		QUERY,
		cache_dir=CACHE_DIR,
		max_listings=MAX_LISTINGS,
		force=args.force,
		include_projection=False,
	)
	level1_result = cached_level1_facets(
		client,
		QUERY,
		cache_dir=CACHE_DIR,
		force=args.force,
	)
	summary = {
		"cache_used": result.cache_used,
		"cache_path": str(result.cache_path),
		"listing_count": len(result.response.data),
		"total_for_sale": result.payload["metadata"]["site_info"]["total_for_sale"],
		"metadata": result.metadata,
		"level1_cache_used": level1_result.cache_used,
		"level1_cache_path": str(level1_result.cache_path),
		"level1_response_count": len(level1_result.collection.responses),
	}
	print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
	main()
