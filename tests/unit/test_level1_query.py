import pytest

from analysis.level1_models import MarketCohort
from visor_api import (
	LEVEL1_FACETS,
	LEVEL1_FACET_SORT,
	LEVEL1_FACET_VALUE_LIMIT,
	LEVEL1_RECENT_SOLD_DAYS,
	VisorListingQuery,
	build_level1_facet_query_plan,
	build_level1_trim_enrichment_query_plan,
)


def market_query(**overrides):
	options = {
		"make": "Honda",
		"model": "Civic",
		"year": "2023,2024",
		"condition": "Used,CPO",
		"location": "80202",
		"radius": "100",
		"max_mileage": "60000",
		"trim": "LX,Sport",
		"sort": "cheapest",
	}
	options.update(overrides)
	return VisorListingQuery.from_options(options)


def test_plan_contains_exactly_three_facet_queries_per_year():
	plan = build_level1_facet_query_plan(market_query())

	assert len(plan) == 6
	assert [(item.year, item.cohort, item.metric) for item in plan] == [
		(2023, MarketCohort.ACTIVE, "price.median"),
		(2023, MarketCohort.ACTIVE, "days_on_market.median"),
		(2023, MarketCohort.RECENTLY_SOLD, "count"),
		(2024, MarketCohort.ACTIVE, "price.median"),
		(2024, MarketCohort.ACTIVE, "days_on_market.median"),
		(2024, MarketCohort.RECENTLY_SOLD, "count"),
	]


def test_every_query_preserves_market_scope_and_facet_settings():
	plan = build_level1_facet_query_plan(market_query())

	for query in plan:
		params = query.api_params()
		assert params["make"] == ("Honda",)
		assert params["model"] == ("Civic",)
		assert params["inventory_type"] == ("certified", "used")
		assert params["postal_code"] == "80202"
		assert params["radius"] == "100"
		assert params["max_mileage"] == "60000"
		assert params["trim"] == ("LX", "Sport")
		assert params["year"] == str(query.year)
		assert params["facets"] == LEVEL1_FACETS
		assert params["facet_value_limit"] == LEVEL1_FACET_VALUE_LIMIT
		assert params["sort"] == LEVEL1_FACET_SORT
		assert "fields" not in params
		assert "include" not in params


def test_selected_trims_explicitly_restrict_every_market_cohort():
	plan = build_level1_facet_query_plan(market_query(year="2024"))

	assert all(
		query.api_params()["trim"] == ("LX", "Sport")
		for query in plan
	)


def test_omitting_selected_trims_discovers_the_whole_model_market():
	plan = build_level1_facet_query_plan(
		market_query(year="2024", trim=None)
	)

	assert all("trim" not in query.api_params() for query in plan)


def test_enrichment_plan_requests_only_active_stats_per_trim():
	plan = build_level1_trim_enrichment_query_plan(
		market_query(year="2024", trim=None),
		{2024: ("LX", "Sport")},
	)

	assert len(plan) == 2
	assert plan[0].api_params()["facets"] == "price,miles,days_on_market"
	assert plan[0].api_params()["metric"] == "count"
	assert plan[0].api_params()["trim"] == ("LX",)
	assert "sold_within_days" not in plan[0].api_params()
	assert plan[1].api_params()["trim"] == ("Sport",)
	assert "sold_within_days" not in plan[1].api_params()


def test_only_recently_sold_query_has_the_fourteen_day_filter():
	plan = build_level1_facet_query_plan(
		market_query(year="2024", sold_within_days="30")
	)

	assert "sold_within_days" not in plan[0].api_params()
	assert "sold_within_days" not in plan[1].api_params()
	assert plan[2].api_params()["sold_within_days"] == LEVEL1_RECENT_SOLD_DAYS


def test_listing_sort_is_replaced_without_mutating_normalized_query():
	query = market_query(year="2024")

	plan = build_level1_facet_query_plan(query)

	assert query.filters["sort"] == "price"
	assert all(item.api_params()["sort"] == "-count" for item in plan)


@pytest.mark.parametrize(
	("options", "message"),
	[
		({"model": "Civic", "year": "2024"}, "require make"),
		({"make": "Honda", "year": "2024"}, "require model"),
		({"make": "Honda", "model": "Civic"}, "model year"),
		({"make": "Honda", "model": "Civic", "year": "unknown"}, "invalid"),
	],
)
def test_plan_rejects_incomplete_market_identity(options, message):
	with pytest.raises(ValueError, match=message):
		build_level1_facet_query_plan(VisorListingQuery.from_options(options))
