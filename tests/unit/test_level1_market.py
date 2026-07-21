from analysis.level1_market import build_market_snapshot
from tests.unit.test_level1_confidence import collection, kbb
from visor_api import VisorListingQuery


def test_snapshot_is_built_from_aggregate_facets_with_full_provenance():
	query = VisorListingQuery.from_options({
		"make": "Honda",
		"model": "Civic",
		"year": "2024",
		"condition": "used",
		"location": "80202",
		"radius": "150",
	})

	snapshot = build_market_snapshot(
		query,
		collection(),
		kbb(),
		generated_at="2026-07-20T15:02:00+00:00",
	)

	assert snapshot.active.inventory_count == 10
	assert snapshot.scope.geography == {
		"postal_code": "80202",
		"distance_type": "radius",
		"distance_value": "150",
		"distance_unit": "mi",
	}
	assert snapshot.year_trim_summaries[0].trim == "Sport"
	assert snapshot.active.asking_price.median == 25_000
	assert snapshot.active.listing_age_days.median == 20
	assert snapshot.recently_sold.time_to_sale_days.median == 15
	assert snapshot.queries[0].request_url is not None
	assert snapshot.queries[0].request_url.startswith("https://api.visor.vin/v1/facets?")
