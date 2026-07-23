from unittest.mock import AsyncMock, MagicMock

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError

from analysis.kbb import (
	_previous_local_trim,
	get_or_fetch_national_pricing,
	get_or_fetch_local_pricing,
	get_price_advisor_values,
	goto_with_retry,
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

	page.wait_for_function.assert_awaited_once()
	page.inner_text.assert_awaited_once_with("body", timeout=10_000)
	assert page.goto.await_args.args[0] == (
		"https://kbb.com/honda/civic/2024/ex-sedan-4d/?zip=80202"
	)
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

	page.wait_for_function.assert_awaited_once()
	assert result[3] is None


async def test_national_pricing_accepts_msrp_only_div_rows():
	page = MagicMock()
	page.goto = AsyncMock()
	page.wait_for_timeout = AsyncMock()
	page.inner_text = AsyncMock(return_value="Hyundai IONIQ 5 Pricing")
	rows = MagicMock()
	rows.first.wait_for = AsyncMock()
	rows.all = AsyncMock()
	heading = MagicMock()
	heading.first = heading
	heading.locator.return_value = rows
	row = MagicMock()
	link = MagicMock()
	link.count = AsyncMock(return_value=0)
	trim = AsyncMock()
	trim.inner_text.return_value = "Ioniq 5 SE"
	msrp = AsyncMock()
	msrp.inner_text.return_value = "$39,100"
	divs = MagicMock()
	divs.all = AsyncMock(return_value=[trim, msrp])
	row.locator.side_effect = lambda selector: link if selector == "a" else divs
	rows.all.return_value = [row]
	page.get_by_role.return_value = heading

	pricing, error = await get_or_fetch_national_pricing(
		page, "Hyundai", "IONIQ 5", "ioniq-5", "2026", {}
	)

	rows.first.wait_for.assert_awaited_once_with(timeout=10_000)
	page.get_by_role.assert_called_once()
	heading.locator.assert_called_once_with("xpath=following::table[1]//tbody/tr")
	assert error is None
	assert pricing[0][0:3] == ("Ioniq 5 SE", "$39,100", None)


async def test_msrp_only_model_prefixed_row_still_checks_local_fpp(monkeypatch):
	cache_entries = {}
	local_lookup = AsyncMock(return_value=(36_000, 40_000, 39_500, None, "local"))
	monkeypatch.setattr(
		"analysis.kbb.get_or_fetch_national_pricing",
		AsyncMock(return_value=([(
			"Ioniq 5 SE",
			"$39,100",
			None,
			"national",
			None,
			"2026-01-01T00:00:00",
		)], None)),
	)
	monkeypatch.setattr("analysis.kbb.get_or_fetch_local_pricing", local_lookup)

	await populate_pricing_for_year(
		AsyncMock(),
		"Hyundai",
		"IONIQ 5",
		"ioniq-5",
		"2026",
		cache_entries,
		{"SE"},
		"80202",
	)

	local_lookup.assert_awaited_once()
	await_args = local_lookup.await_args
	assert await_args is not None
	assert await_args.args[4] == "SE"
	entry = cache_entries["2026 Hyundai IONIQ 5 SE"]
	assert entry["msrp"] == 39_100
	assert entry["fpp_natl"] is None
	assert entry["fpp_local"] == 39_500


async def test_partial_national_table_checks_every_requested_trim_locally(monkeypatch):
	local_lookup = AsyncMock(return_value=(40_000, 48_000, 45_000, None, "local"))
	monkeypatch.setattr(
		"analysis.kbb.get_or_fetch_national_pricing",
		AsyncMock(return_value=([(
			"Ioniq 5 SE",
			"$39,100",
			None,
			"national",
			None,
			"2026-01-01T00:00:00",
		)], None)),
	)
	monkeypatch.setattr("analysis.kbb.get_or_fetch_local_pricing", local_lookup)

	await populate_pricing_for_year(
		AsyncMock(),
		"Hyundai",
		"IONIQ 5",
		"ioniq-5",
		"2026",
		{},
		{"SE", "XRT"},
		"80202",
	)

	checked_trims = {call.args[4] for call in local_lookup.await_args_list}
	assert checked_trims == {"SE", "XRT"}


async def test_navigation_retries_share_one_total_timeout(monkeypatch):
	timeouts = []

	class TimeoutContext:
		async def __aenter__(self):
			return None

		async def __aexit__(self, *args):
			return False

	def timeout(seconds):
		timeouts.append(seconds)
		return TimeoutContext()

	page = AsyncMock()
	# Retry handling is specifically for Playwright transport/navigation errors.
	page.goto.side_effect = [PlaywrightError("first"), None]
	monkeypatch.setattr("analysis.kbb.asyncio.timeout", timeout)

	await goto_with_retry(page, "https://kbb.test/vehicle")

	assert timeouts == [30]
	assert page.goto.await_count == 2
	assert all(
		call.kwargs["timeout"] == 10_000
		for call in page.goto.await_args_list
	)


async def test_price_advisor_allows_dynamic_content_thirty_seconds():
	page = MagicMock()
	advisor = MagicMock()
	advisor.first = advisor
	advisor.wait_for = AsyncMock()
	advisor.get_attribute = AsyncMock(return_value="https://kbb.test/advisor.svg")
	page.locator.return_value = advisor
	svg_page = MagicMock()
	svg_page.goto = AsyncMock()
	svg_page.close = AsyncMock()
	texts = MagicMock()
	texts.all_text_contents = AsyncMock(return_value=[
		"Fair Market Range",
		"$44,100 - $46,800",
		"Fair Purchase Price",
		"$45,500",
		"Invoice",
		"$47,685",
	])
	svg_page.locator.return_value = texts
	page.context.new_page = AsyncMock(return_value=svg_page)

	result = await get_price_advisor_values(page, "80013")

	advisor.wait_for.assert_awaited_once_with(state="attached", timeout=30_000)
	advisor.get_attribute.assert_awaited_once_with("data", timeout=30_000)
	assert svg_page.goto.await_args.args[0] == (
		"https://kbb.test/advisor.svg?zipcode=80013"
	)
	assert result == (44_100, 46_800, 45_500)


async def test_price_advisor_reports_unavailable_without_inventing_values(caplog):
	page = MagicMock()
	advisor = MagicMock()
	advisor.first = advisor
	advisor.wait_for = AsyncMock()
	advisor.get_attribute = AsyncMock(
		return_value="https://kbb.test/advisor.svg?zipcode=80201"
	)
	page.locator.return_value = advisor
	svg_page = MagicMock()
	svg_page.goto = AsyncMock()
	svg_page.close = AsyncMock()
	texts = MagicMock()
	texts.all_text_contents = AsyncMock(return_value=[
		"unavailable", "Fair Market Range", "MSRP", "$48,150"
	])
	svg_page.locator.return_value = texts
	page.context.new_page = AsyncMock(return_value=svg_page)

	result = await get_price_advisor_values(page, "80202")

	assert result == (None, None, None)
	assert "reports local pricing as unavailable" in caplog.text


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
