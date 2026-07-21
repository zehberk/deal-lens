import logging

from datetime import datetime

from analysis.level1_kbb import (
	get_level1_kbb_valuations,
	level1_kbb_model_variations,
	level1_year_trims,
	map_level1_kbb_valuations,
)
from utils.cache import is_entry_fresh
from utils.constants import KBB_CACHE_TTL
from visor_api import (
	Level1FacetCollection,
	Level1TrimFacetBucket,
	Level1YearFacetResult,
)


def bucket(year, trim):
	return Level1TrimFacetBucket(
		year=year,
		trim=trim,
		active_inventory_count=1,
		recently_sold_inventory_count=1,
		active_price_median=20_000,
		active_days_on_market_median=20,
		recently_sold_days_on_market_median=15,
	)


def collection():
	return Level1FacetCollection(
		years=(
			Level1YearFacetResult(
				year=2023,
				active_inventory_count=1,
				recently_sold_inventory_count=1,
				trims=(bucket(2023, "Sport"),),
			),
			Level1YearFacetResult(
				year=2024,
				active_inventory_count=2,
				recently_sold_inventory_count=1,
				trims=(bucket(2024, "LX"), bucket(2024, "LX")),
			),
		),
		responses=(),
	)


def entry(year, trim):
	value = {
		"model": "Civic",
		"kbb_trim": f"{year} Honda Civic {trim}",
		"msrp": 25_000,
		"fpp_natl": 22_000,
		"fmr_low": 20_000,
		"fmr_high": 24_000,
		"fpp_local": 22_500,
		"fmv": 18_000,
		"natl_source": f"https://kbb.com/honda/civic/{year}/",
		"local_source": f"https://kbb.com/honda/civic/{year}/{trim.lower()}/",
		"postal_code": "80202",
	}
	return value


def test_unique_year_trim_combinations_come_directly_from_facets():
	assert level1_year_trims(collection()) == {
		2023: ("Sport",),
		2024: ("LX",),
	}


def test_kbb_model_variations_are_resolved_before_pricing_lookup():
	result = level1_kbb_model_variations(
		"Honda",
		"Civic",
		{2023: ("EX", "Type-R")},
		{"2023": {"Honda": ["Civic", "Civic Hybrid", "Civic Type R"]}},
	)

	assert result == {
		(2023, "EX"): "Civic",
		(2023, "Type-R"): "Civic Type R",
	}


def test_missing_variation_cache_keeps_base_model():
	result = level1_kbb_model_variations(
		"Honda",
		"Civic",
		{2023: ("Type-R",)},
		{},
	)

	assert result == {(2023, "Type-R"): "Civic"}


def test_mapping_reads_pricing_from_resolved_model_variation():
	entries = {
		"2023 Honda Civic Type R Type R Hatchback 4D": entry(
			2023, "Type R Hatchback 4D"
		),
	}
	entries["2023 Honda Civic Type R Type R Hatchback 4D"]["model"] = "Civic Type R"
	entries["2023 Honda Civic Type R Type R Hatchback 4D"]["natl_source"] = (
		"https://kbb.com/honda/civic-type-r/2023/"
	)
	result = map_level1_kbb_valuations(
		"Honda",
		"Civic",
		{2023: ("Type-R",)},
		entries,
		model_by_year_trim={(2023, "Type-R"): "Civic Type R"},
	)

	assert result.failures == ()
	assert result.matches[0].kbb_trim == "Type R Hatchback 4D"


def test_mapping_keeps_model_years_separate_and_rejects_unrelated_trims():
	entries = {
		"2023 Honda Civic Sport": entry(2023, "Sport"),
		"2024 Honda Civic LX": entry(2024, "LX"),
	}
	result = map_level1_kbb_valuations(
		"Honda",
		"Civic",
		{2023: ("Sport",), 2024: ("LX", "Touring")},
		entries,
	)

	assert [(item.year, item.visor_trim) for item in result.matches] == [
		(2023, "Sport"),
		(2024, "LX"),
	]
	assert result.matches[0].valuation.fpp_local == 22_500
	assert result.matches[1].valuation.fpp_local == 22_500
	assert result.failures[0].visor_trim == "Touring"
	assert result.failures[0].reason == "kbb_trim_not_found"


def test_missing_year_is_recorded_instead_of_silently_dropped():
	result = map_level1_kbb_valuations(
		"Honda", "Civic", {2025: ("Sport",)}, {}
	)

	assert result.matches == ()
	assert result.failures[0].reason == "kbb_trim_not_found"


async def test_fresh_seven_day_cache_avoids_kbb_browser(monkeypatch, caplog):
	cached_entry = entry(2023, "Sport")
	cached_entry["natl_timestamp"] = datetime.now().isoformat()
	cached_entry["local_timestamp"] = datetime.now().isoformat()

	async def unexpected_browser():
		raise AssertionError("fresh KBB data should not open a browser")

	monkeypatch.setattr(
		"analysis.level1_kbb.create_kbb_browser", unexpected_browser
	)
	with caplog.at_level(logging.INFO, logger="analysis.level1_kbb"):
		result = await get_level1_kbb_valuations(
			"Honda",
			"Civic",
			Level1FacetCollection(
				years=(collection().years[0],),
				responses=(),
			),
			{"entries": {"2023 Honda Civic Sport": cached_entry}},
			postal_code="80202",
		)

	assert result.matches[0].visor_trim == "Sport"
	assert "1 matches, 0 failures" in caplog.text


def test_kbb_cache_ttl_is_seven_days():
	assert KBB_CACHE_TTL.days == 7
	now = datetime.now()
	entry_data = {
		"natl_timestamp": (now - KBB_CACHE_TTL).isoformat(),
		"local_timestamp": now.isoformat(),
	}

	assert is_entry_fresh(entry_data) is False
