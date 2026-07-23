from unittest.mock import AsyncMock

from playwright.async_api import TimeoutError

from analysis.kbb import (
	_previous_local_trim,
	get_or_fetch_local_pricing,
	populate_pricing_for_year,
)


async def test_local_pricing_waits_for_delayed_resale_value(monkeypatch):
	page = AsyncMock()
	page.inner_text.return_value = "This car has a current resale value of $23,100."
	monkeypatch.setattr(
		"analysis.kbb.get_price_advisor_values",
		AsyncMock(return_value=(20_000, 24_000, 22_500)),
	)

	result = await get_or_fetch_local_pricing(
		page,
		"2024",
		"Honda",
		"civic",
		"EX Sedan 4D",
		"2024 Honda Civic EX Sedan 4D",
		{},
		"80202",
	)

	page.inner_text.assert_awaited_once_with("div.css-fbyg3h", timeout=10000)
	assert result[3] == 23_100


async def test_missing_resale_value_continues_after_wait(monkeypatch):
	page = AsyncMock()
	page.inner_text.side_effect = TimeoutError("resale value was not rendered")
	monkeypatch.setattr(
		"analysis.kbb.get_price_advisor_values",
		AsyncMock(return_value=(20_000, 24_000, 22_500)),
	)

	result = await get_or_fetch_local_pricing(
		page,
		"2024",
		"Honda",
		"civic",
		"EX Sedan 4D",
		"2024 Honda Civic EX Sedan 4D",
		{},
		"80202",
	)

	page.inner_text.assert_awaited_once_with("div.css-fbyg3h", timeout=10000)
	assert result[3] is None


def test_previous_year_variation_supplies_sole_local_trim():
	cache_entries = {
		"2023 Honda Civic Type R Hatchback Sedan 4D": {
			"model": "Civic Type R",
			"fpp_local": 38_380,
			"local_source": (
				"https://kbb.com/honda/civic-type-r/2023/hatchback-sedan-4d/"
			),
		},
	}

	assert _previous_local_trim(
		cache_entries, "Honda", "Civic Type R", "2025", "Type-R"
	) == "Hatchback Sedan 4D"


def test_previous_year_base_uses_kbb_base_body_style():
	cache_entries = {
		"2024 Subaru Outback Wagon 4D": {
			"model": "Outback",
			"fpp_local": 26_100,
			"local_source": "https://kbb.com/subaru/outback/2024/wagon-4d/",
		},
		"2024 Subaru Outback Premium Wagon 4D": {
			"model": "Outback",
			"fpp_local": 28_400,
			"local_source": (
				"https://kbb.com/subaru/outback/2024/premium-wagon-4d/"
			),
		},
	}

	assert _previous_local_trim(
		cache_entries, "Subaru", "Outback", "2025", "Base"
	) == "Wagon 4D"


async def test_missing_national_table_attempts_direct_local_trim(monkeypatch):
	cache_entries = {}
	local_lookup = AsyncMock(return_value=(24_000, 28_000, 26_100, None, "local"))
	monkeypatch.setattr(
		"analysis.kbb.get_or_fetch_national_pricing",
		AsyncMock(return_value=([], "national table unavailable")),
	)
	monkeypatch.setattr("analysis.kbb.get_or_fetch_local_pricing", local_lookup)

	await populate_pricing_for_year(
		AsyncMock(),
		"Honda",
		"Civic",
		"civic",
		"2026",
		cache_entries,
		{"Sport"},
		"80202",
	)

	local_lookup.assert_awaited_once()
	await_args = local_lookup.await_args
	assert await_args is not None
	assert await_args.args[4] == "Sport"
	entry = cache_entries["2026 Honda Civic Sport"]
	assert entry["fpp_local"] == 26_100
	assert "skip_reason" not in entry
