"""Compare Visor facet responses with and without explicit trim filters."""

import json
import sys

from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from visor_api import VisorClient
from deal_lens.config import get_visor_api_key


BASE_FILTERS = {
	"year": 2020,
	"make": "Honda",
	"model": "Civic",
	"facets": "model,trim,days_on_market",
}

EXPERIMENTS = {
	"multiple_trims": {**BASE_FILTERS, "trim": ("LX", "Sport")},
	"no_trim_filter": BASE_FILTERS,
}


def summarize(response: dict[str, Any]) -> dict[str, Any]:
	"""Return only the aggregate fields needed to compare facet behavior."""
	data = response.get("data") if isinstance(response.get("data"), dict) else {}
	category_facets = (
		data.get("facets") if isinstance(data.get("facets"), dict) else {}
	)
	range_facets = (
		data.get("range_facets")
		if isinstance(data.get("range_facets"), dict)
		else {}
	)
	stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
	return {
		"total": data.get("total"),
		"models": category_facets.get("model"),
		"trims": category_facets.get("trim"),
		"days_on_market_range": range_facets.get("days_on_market"),
		"days_on_market_stats": stats.get("days_on_market"),
		"meta": response.get("meta"),
	}


def main() -> None:
	client = VisorClient(get_visor_api_key(env_file=PROJECT_ROOT / "api.env"))
	results = {}
	for name, params in EXPERIMENTS.items():
		results[name] = {
			"request": {
				key: list(value) if isinstance(value, tuple) else value
				for key, value in params.items()
			},
			"response": summarize(client.filter_facets(params)),
		}

	print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
	main()
