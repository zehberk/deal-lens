from datetime import datetime, timezone

import pytest

from visor_api import (
	FacetResponse,
	Level1FacetResponseError,
	VisorListingQuery,
	collect_level1_facets,
)


def facet_response(metric, total, trims, *, sort="-count"):
	return FacetResponse.from_dict({
		"data": {
			"total": total,
			"facets": {
				"trim": [
					{
						"value": trim,
						"count": count,
						"metric": (
							None if value == "missing_metric" else {
								"name": metric,
								"value": None if isinstance(value, str) else value,
								"null_reason": value if isinstance(value, str) else None,
							}
						),
					}
					for trim, count, value in trims
				]
			},
			"range_facets": {},
			"stats": {},
		},
		"meta": {
			"facets": ["trim", "price", "miles", "days_on_market"],
			"metric": metric,
			"sort": sort,
			"minimum_metric_count": 5,
		},
	})


class FakeFacetClient:
	def __init__(self, responses):
		self.responses = list(responses)
		self.facet_calls = []

	def filter_facets_model(self, params=None):
		self.facet_calls.append(dict(params or {}))
		return self.responses.pop(0)

	def filter_listings(self, params=None):
		raise AssertionError("Level 1 facet service must not request listings")


def query(year="2024"):
	return VisorListingQuery.from_options({
		"make": "Honda",
		"model": "Civic",
		"year": year,
		"condition": "used",
		"location": "80202",
	})


def test_service_executes_three_calls_per_year_without_listing_requests():
	responses = []
	for _ in range(2):
		responses.extend((
			facet_response("price.median", 10, [("LX", 10, 25_000)]),
			facet_response("days_on_market.median", 10, [("LX", 10, 30)]),
			facet_response("days_on_market.median", 4, [("LX", 4, 22)]),
		))
	client = FakeFacetClient(responses)

	result = collect_level1_facets(
		client,
		query("2023,2024"),
		clock=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
	)

	assert len(client.facet_calls) == 6
	assert len(result.responses) == 6
	assert [item.year for item in result.years] == [2023, 2024]
	assert all(
		item.retrieved_at == "2026-07-20T00:00:00+00:00"
		for item in result.responses
	)


def test_service_matches_partial_trim_buckets_and_records_missing_reasons():
	client = FakeFacetClient([
		facet_response("price.median", 12, [
			("LX", 10, 25_000),
			("Sport", 2, "below_minimum_metric_count"),
		]),
		facet_response("days_on_market.median", 12, [("LX", 10, 30)]),
		facet_response("days_on_market.median", 3, [("Sport", 3, 20)]),
	])

	result = collect_level1_facets(client, query())
	by_trim = {item.trim: item for item in result.years[0].trims}

	assert set(by_trim) == {"LX", "Sport"}
	assert by_trim["Sport"].active_inventory_count == 2
	assert by_trim["Sport"].active_price_median is None
	assert by_trim["Sport"].active_price_missing_reason == "below_minimum_metric_count"
	assert by_trim["Sport"].active_days_on_market_missing_reason == "trim_bucket_not_returned"
	assert by_trim["LX"].recently_sold_inventory_count is None
	assert by_trim["LX"].recently_sold_days_on_market_missing_reason == "trim_bucket_not_returned"


def test_service_distinguishes_missing_metric_object_from_missing_value():
	client = FakeFacetClient([
		facet_response("price.median", 2, [("Sport", 2, "missing_metric")]),
		facet_response("days_on_market.median", 2, [("Sport", 2, 12)]),
		facet_response("days_on_market.median", 0, []),
	])

	bucket = collect_level1_facets(client, query()).years[0].trims[0]

	assert bucket.active_price_missing_reason == "metric_not_returned"


def test_service_rejects_response_for_the_wrong_metric():
	responses = [
		facet_response("days_on_market.median", 10, [("LX", 10, 30)]),
		facet_response("days_on_market.median", 10, [("LX", 10, 30)]),
		facet_response("days_on_market.median", 4, [("LX", 4, 22)]),
	]

	with pytest.raises(Level1FacetResponseError, match="expected metric"):
		collect_level1_facets(FakeFacetClient(responses), query())


def test_service_tolerates_inventory_changes_between_facet_calls():
	client = FakeFacetClient([
		facet_response("price.median", 10, [("LX", 10, 25_000)]),
		facet_response("days_on_market.median", 9, [("LX", 9, 30)]),
		facet_response("days_on_market.median", 4, [("LX", 4, 22)]),
	])

	result = collect_level1_facets(client, query())

	assert result.years[0].active_inventory_count == 10
	assert result.years[0].trims[0].active_inventory_count == 10
	assert result.years[0].trims[0].active_days_on_market_median == 30


def test_service_rejects_naive_retrieval_timestamps():
	client = FakeFacetClient([
		facet_response("price.median", 0, []),
		facet_response("days_on_market.median", 0, []),
		facet_response("days_on_market.median", 0, []),
	])

	with pytest.raises(ValueError, match="aware datetime"):
		collect_level1_facets(client, query(), clock=lambda: datetime(2026, 7, 20))
