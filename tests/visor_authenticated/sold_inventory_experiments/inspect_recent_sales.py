"""Inspect recent sold inventory through listings and facets API calls."""

import json
import sys

from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from visor_api import VisorClient
from deal_lens.config import get_visor_api_key


MARKET_FILTERS = {
	"year": 2020,
	"make": "Honda",
	"model": "Civic",
	"sold_within_days": 14,
}


def summarize_listings(response: dict[str, Any]) -> dict[str, Any]:
	rows = response.get("data") if isinstance(response.get("data"), list) else []
	return {
		"total": (response.get("pagination") or {}).get("total"),
		"samples": [
			{
				"trim": row.get("trim"),
				"status": row.get("status"),
				"inventory_status": row.get("inventory_status"),
				"sold_date": row.get("sold_date"),
				"days_on_market": row.get("days_on_market"),
			}
			for row in rows
			if isinstance(row, dict)
		],
		"meta": response.get("meta"),
	}


def summarize_facets(response: dict[str, Any]) -> dict[str, Any]:
	data = response.get("data") if isinstance(response.get("data"), dict) else {}
	category_facets = (
		data.get("facets") if isinstance(data.get("facets"), dict) else {}
	)
	stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
	return {
		"total": data.get("total"),
		"trims": category_facets.get("trim"),
		"days_on_market": stats.get("days_on_market"),
		"meta": response.get("meta"),
	}


def main() -> None:
	client = VisorClient(get_visor_api_key(env_file=PROJECT_ROOT / "api.env"))
	listings = client.filter_listings({
		**MARKET_FILTERS,
		"fields": "default,days_on_market",
		"limit": 3,
	})
	facets = client.filter_facets({
		**MARKET_FILTERS,
		"facets": "trim,days_on_market",
	})
	print(json.dumps({
		"market_filters": MARKET_FILTERS,
		"listings": summarize_listings(listings),
		"facets": summarize_facets(facets),
	}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
	main()
