import asyncio

from unittest.mock import AsyncMock, MagicMock
from contextlib import nullcontext

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError

from analysis.kbb import (
	_configuration_matches_listing,
	_previous_local_trim,
	_used_listing_has_cached_pricing,
	configure_kbb_page_diagnostics,
	create_kbb_browser,
	get_or_fetch_national_pricing,
	get_or_fetch_local_pricing,
	get_price_advisor_values,
	get_used_style_url_from_vins,
	get_vin_first_pricing_data,
	goto_with_retry,
	populate_pricing_for_year,
)


def _fake_kbb_browser():
	request = MagicMock()
	request.dispose = AsyncMock()
	browser = MagicMock()
	browser.close = AsyncMock()
	context = MagicMock()
	context.close = AsyncMock()
	page = MagicMock()
	page.close = AsyncMock()
	return request, browser, context, page


async def test_vin_first_pricing_reuses_configuration_and_enriches_national(
	monkeypatch,
):
	first = {
		"id": "one", "vin": "VIN1", "year": 2025, "condition": "Used",
		"trim": "SE", "fuel_type": "plug-in hybrid", "powertrain_type": "phev",
		"body_style": "hatchback",
	}
	second = {**first, "id": "two", "vin": "VIN2"}
	resolve = AsyncMock(return_value=(
		"SE Hatchback 4D",
		"https://kbb.com/toyota/prius-plug-in-hybrid/2025/se-hatchback-4d/",
	))
	local = AsyncMock(return_value=(30_000, 34_000, 32_500, 31_000, (
		"https://kbb.com/toyota/prius-plug-in-hybrid/2025/se-hatchback-4d/"
	)))
	national = AsyncMock(return_value=([(
		"SE", "$34,510", "$32,100", "national", "/se/", "2026-01-01"
	)], None))
	monkeypatch.setattr("analysis.kbb.create_kbb_browser", AsyncMock(
		return_value=_fake_kbb_browser()
	))
	monkeypatch.setattr("analysis.kbb.get_used_style_url_from_vins", resolve)
	monkeypatch.setattr("analysis.kbb._get_local_pricing_with_progress", local)
	monkeypatch.setattr("analysis.kbb.get_or_fetch_national_pricing", national)
	monkeypatch.setattr("analysis.kbb.save_cache", lambda _cache: None)
	cache = {}

	valuations = await get_vin_first_pricing_data(
		"Toyota", "Prius", [first, second],
		{"2025 Toyota Prius Plug-in Hybrid": [first, second]}, cache,
	)
	await get_vin_first_pricing_data(
		"Toyota", "Prius", [first, second],
		{"2025 Toyota Prius Plug-in Hybrid": [first, second]}, cache,
	)

	resolve.assert_awaited_once()
	local.assert_awaited_once()
	national.assert_awaited_once()
	assert first["kbb_cache_key"] == second["kbb_cache_key"]
	assert list(cache["level23_entries"]) == [
		"2025 Toyota Prius Plug-in Hybrid SE Hatchback 4D"
	]
	entry = cache["level23_entries"][first["kbb_cache_key"]]
	assert entry["pricing_basis"] == "vin"
	assert entry["fpp_local"] == 32_500
	assert entry["fpp_natl"] == 32_100
	assert valuations[0].kbb_trim == first["kbb_cache_key"]


async def test_vin_first_does_not_fetch_local_price_without_vin_resolution(
	monkeypatch,
):
	listing = {
		"id": "one", "vin": "VIN1", "year": 2025,
		"condition": "Used", "trim": "SE",
		"trim_version": "SE Plug-in Hybrid",
	}
	local = AsyncMock()
	monkeypatch.setattr("analysis.kbb.create_kbb_browser", AsyncMock(
		return_value=_fake_kbb_browser()
	))
	monkeypatch.setattr(
		"analysis.kbb.get_used_style_url_from_vins", AsyncMock(return_value=None)
	)
	monkeypatch.setattr("analysis.kbb._get_local_pricing_with_progress", local)
	monkeypatch.setattr("analysis.kbb.get_or_fetch_national_pricing", AsyncMock(
		return_value=([(
			"SE", "$34,510", "$32,100", "national", "/se/", "2026-01-01"
		)], None)
	))
	monkeypatch.setattr("analysis.kbb.save_cache", lambda _cache: None)
	cache = {}

	await get_vin_first_pricing_data(
		"Toyota", "Prius", [listing],
		{"2025 Toyota Prius Plug-in Hybrid": [listing]}, cache,
	)

	local.assert_not_awaited()
	entry = cache["level23_entries"][listing["kbb_cache_key"]]
	assert entry["pricing_basis"] == "national"
	assert entry["fpp_local"] is None
	assert entry["local_source"] is None


def test_configuration_matching_treats_optional_evidence_as_constraints():
	configuration = {
		"style": "SE Hatchback 4D",
		"fuel_type": "plug-in hybrid",
		"body_style": "hatchback",
	}

	assert _configuration_matches_listing(configuration, {
		"trim": "SE", "trim_version": "SE Plug-in Hybrid",
	})
	assert not _configuration_matches_listing(configuration, {
		"trim": "SE", "fuel_type": "hybrid",
	})


async def test_kbb_browser_launches_headless(monkeypatch):
	playwright = MagicMock()
	playwright.request.new_context = AsyncMock(return_value=MagicMock())
	browser = MagicMock()
	context = MagicMock()
	context.route = AsyncMock()
	page = MagicMock()
	context.new_page = AsyncMock(return_value=page)
	browser.new_context = AsyncMock(return_value=context)
	playwright.chromium.launch = AsyncMock(return_value=browser)
	playwright_starter = MagicMock()
	playwright_starter.start = AsyncMock(return_value=playwright)
	playwright_factory = MagicMock(return_value=playwright_starter)
	monkeypatch.setattr("analysis.kbb.async_playwright", playwright_factory)

	await create_kbb_browser()

	launch_call = playwright.chromium.launch.await_args
	assert launch_call is not None
	assert launch_call.kwargs["headless"] is True
	assert launch_call.kwargs["channel"] == "chrome"
	browser.new_context.assert_awaited_once()
	context_call = browser.new_context.await_args
	assert context_call is not None
	assert "HeadlessChrome" not in context_call.kwargs["user_agent"]
	assert {call.args[0] for call in page.on.call_args_list} == {
		"console",
		"pageerror",
		"requestfailed",
		"response",
	}


def test_failed_used_cache_entry_does_not_suppress_retry(monkeypatch):
	monkeypatch.setattr("analysis.kbb.is_entry_fresh", lambda entry: True)
	entries = {
		"2025 INFINITI QX55 luxe awd": {
			"model": "QX55",
			"pricing_basis": "used",
			"skip_reason": "KBB used style could not be resolved from VIN.",
			"msrp": None,
			"fpp_natl": None,
			"fpp_local": None,
			"fmv": None,
		},
	}
	listing = {"year": 2025, "trim": "luxe awd"}

	assert not _used_listing_has_cached_pricing(
		listing, "INFINITI", "QX55", entries
	)


async def test_vin_lookup_resolves_exact_used_style_url():
	page = MagicMock()
	page.goto = AsyncMock()
	page.wait_for_url = AsyncMock()
	page.wait_for_function = AsyncMock()
	page.inner_text = AsyncMock(return_value=(
		"2025 INFINITI QX55\nStyle:\nLUXE Sport Utility 4D\nEngine:\n2.0L"
	))
	page.url = (
		"https://www.kbb.com/infiniti/qx55/2025/vin/"
		"?intent=trade-in-sell&vin=test"
	)

	result = await get_used_style_url_from_vins(
		page, "2025", "INFINITI", "qx55", ["TESTVIN"]
	)

	assert result == (
		"LUXE Sport Utility 4D",
		"https://kbb.com/infiniti/qx55/2025/luxe-sport-utility-4d/",
	)
	page.goto.assert_awaited_once_with(
		"https://kbb.com/infiniti/qx55/2025/vin/"
		"?intent=trade-in-sell&vin=TESTVIN",
		wait_until="domcontentloaded",
		timeout=10_000,
	)


async def test_vin_lookup_stops_after_three_failed_vins(caplog):
	caplog.set_level("INFO", logger="analysis.kbb")
	page = MagicMock()
	page.goto = AsyncMock()
	page.wait_for_function = AsyncMock(side_effect=TimeoutError("style unavailable"))
	page.url = "https://kbb.com/hyundai/ioniq-5/2024/vin/"
	page.evaluate = AsyncMock(return_value={"vinInput": {"disabled": True}})

	result = await get_used_style_url_from_vins(
		page,
		"2024",
		"Hyundai",
		"ioniq-5",
		["VIN1", "VIN2", "VIN3", "VIN4"],
	)

	assert result is None
	assert page.goto.await_count == 3
	assert page.evaluate.await_count == 3
	assert "during waiting for VIN style" in caplog.text
	assert "KBB VIN diagnostic for VIN1" in caplog.text


async def test_unhealthy_kbb_app_stops_after_first_vin():
	page = MagicMock()
	page.goto = AsyncMock()
	page.url = "https://kbb.com/hyundai/ioniq-5/2024/vin/"
	page.evaluate = AsyncMock(return_value={"challenge": False})
	configure_kbb_page_diagnostics(page)
	page_error = next(
		call.args[1] for call in page.on.call_args_list
		if call.args[0] == "pageerror"
	)

	async def fail_with_page_error(*_args, **_kwargs):
		page_error(RuntimeError("Unexpected end of JSON input"))
		raise TimeoutError("style unavailable")

	page.wait_for_function = AsyncMock(side_effect=fail_with_page_error)

	result = await get_used_style_url_from_vins(
		page, "2024", "Hyundai", "ioniq-5", ["VIN1", "VIN2", "VIN3"]
	)

	assert result is None
	page.goto.assert_awaited_once()


async def test_each_vin_lookup_has_its_own_time_limit(monkeypatch):
	page = MagicMock()

	async def never_finishes(*_args, **_kwargs):
		await asyncio.Event().wait()

	page.goto = AsyncMock(side_effect=never_finishes)
	monkeypatch.setattr("analysis.kbb.KBB_USED_VIN_ATTEMPT_TIMEOUT_SECONDS", 0.01)

	result = await get_used_style_url_from_vins(
		page, "2024", "Hyundai", "ioniq-5", ["VIN1"]
	)

	assert result is None
	page.goto.assert_awaited_once()


async def test_used_lookup_rejects_new_kbb_page(monkeypatch):
	page = MagicMock()
	page.goto = AsyncMock()
	heading = MagicMock()
	heading.first = heading
	heading.inner_text = AsyncMock(return_value="2025 INFINITI QX55 LUXE")
	page.locator.return_value = heading
	price_advisor = AsyncMock(return_value=(40_000, 46_000, 45_600))
	monkeypatch.setattr("analysis.kbb.get_price_advisor_values", price_advisor)

	result = await get_or_fetch_local_pricing(
		page,
		"2025",
		"INFINITI",
		"qx55",
		"LUXE Sport Utility 4D",
		"2025 INFINITI QX55 LUXE",
		{},
		source_url="https://kbb.com/infiniti/qx55/2025/luxe/",
		expect_used=True,
	)

	assert result == (None, None, None, None, None)
	price_advisor.assert_not_awaited()


async def test_used_trim_uses_vin_style_and_retains_national_fpp(monkeypatch):
	cache_entries = {}
	local_lookup = AsyncMock(return_value=(32_000, 36_000, 34_300, 32_800, (
		"https://kbb.com/infiniti/qx55/2025/luxe-sport-utility-4d/"
	)))
	monkeypatch.setattr(
		"analysis.kbb.get_or_fetch_national_pricing",
		AsyncMock(return_value=([(
			"LUXE",
			"$51,500",
			"$45,600",
			"https://kbb.com/infiniti/qx55/2025/",
			"/infiniti/qx55/2025/luxe/",
			"2026-01-01T00:00:00",
		)], None)),
	)
	monkeypatch.setattr("analysis.kbb.get_or_fetch_local_pricing", local_lookup)

	await populate_pricing_for_year(
		AsyncMock(),
		"INFINITI",
		"QX55",
		"qx55",
		"2025",
		cache_entries,
		{"luxe"},
		used_style_urls={"luxe": (
			"LUXE Sport Utility 4D",
			"https://kbb.com/infiniti/qx55/2025/luxe-sport-utility-4d/",
		)},
	)

	await_args = local_lookup.await_args
	assert await_args is not None
	assert await_args.args[4] == "LUXE Sport Utility 4D"
	assert await_args.kwargs == {
		"source_url": (
			"https://kbb.com/infiniti/qx55/2025/luxe-sport-utility-4d/"
		),
		"expect_used": True,
	}
	entry = cache_entries["2025 INFINITI QX55 LUXE"]
	assert entry["pricing_basis"] == "used"
	assert entry["fpp_natl"] == 45_600
	assert entry["fpp_local"] == 34_300


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
	)

	page.wait_for_function.assert_awaited_once()
	page.inner_text.assert_awaited_once_with("body", timeout=10_000)
	assert page.goto.await_args.args[0] == (
		"https://kbb.com/honda/civic/2024/ex-sedan-4d/"
	)
	assert "zip=" not in page.goto.await_args.args[0]
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
	)

	page.wait_for_function.assert_awaited_once()
	assert result[3] is None


async def test_national_pricing_accepts_msrp_only_div_rows(caplog):
	caplog.set_level("DEBUG", logger="analysis.kbb")
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
	assert "KBB national source for Ioniq 5 SE" in caplog.text
	assert "https://kbb.com/hyundai/ioniq-5/2026/" in caplog.text


async def test_empty_national_cache_entry_is_refetched(monkeypatch):
	page = MagicMock()
	page.goto = AsyncMock()
	page.wait_for_timeout = AsyncMock()
	page.inner_text = AsyncMock(return_value="INFINITI QX55 Pricing")
	rows = MagicMock()
	rows.first.wait_for = AsyncMock(side_effect=TimeoutError("missing table"))
	heading = MagicMock()
	heading.first = heading
	heading.locator.return_value = rows
	page.get_by_role.return_value = heading
	monkeypatch.setattr("analysis.kbb.is_natl_fresh", lambda entry: True)
	cache_entries = {
		"2025 INFINITI QX55 Luxe": {
			"model": "QX55",
			"kbb_trim": "2025 INFINITI QX55 Luxe",
			"msrp": None,
			"fpp_natl": None,
			"natl_source": "https://kbb.com/infiniti/qx55/2025/",
			"local_source": None,
			"natl_timestamp": "2026-07-26T13:07:16",
		},
	}

	pricing, _ = await get_or_fetch_national_pricing(
		page, "INFINITI", "QX55", "qx55", "2025", cache_entries
	)

	page.goto.assert_awaited_once()
	assert pricing == []


async def test_msrp_only_model_prefixed_row_still_checks_local_fpp(
	monkeypatch, caplog
):
	caplog.set_level("INFO", logger="analysis.kbb")
	cache_entries = {}
	progress = MagicMock()
	progress.status.return_value = nullcontext()
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
		progress,
	)

	local_lookup.assert_awaited_once()
	await_args = local_lookup.await_args
	assert await_args is not None
	assert await_args.args[4] == "SE"
	entry = cache_entries["2026 Hyundai IONIQ 5 SE"]
	assert entry["msrp"] == 39_100
	assert entry["fpp_natl"] is None
	assert entry["fpp_local"] == 39_500
	progress.status.assert_called_once_with("KBB local pricing: 2026 SE")
	assert "2026 Hyundai IONIQ 5 (1 trim found)" in caplog.text
	assert "MSRP: 1/1 available | National FPP: 0/1 available" in caplog.text
	assert "SE: Local FPP=$39,500 | FMV=unavailable" in caplog.text
	assert "National FPP unavailable" in caplog.text


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
	)

	checked_trims = {call.args[4] for call in local_lookup.await_args_list}
	assert checked_trims == {"SE", "XRT"}


async def test_detailed_trim_alias_uses_matching_national_table_row(monkeypatch):
	cache_entries = {}
	local_lookup = AsyncMock(
		return_value=(29_000, 32_000, 30_520, None, "local")
	)
	monkeypatch.setattr(
		"analysis.kbb.get_or_fetch_national_pricing",
		AsyncMock(return_value=([(
			"Limited Sport Utility 4D",
			"$54,875",
			"$30,400",
			"https://kbb.com/hyundai/ioniq-5/2024/",
			"/hyundai/ioniq-5/2024/limited-sport-utility-4d/",
			"2026-01-01T00:00:00",
		)], None)),
	)
	monkeypatch.setattr("analysis.kbb.get_or_fetch_local_pricing", local_lookup)

	await populate_pricing_for_year(
		AsyncMock(),
		"Hyundai",
		"IONIQ 5",
		"ioniq-5",
		"2024",
		cache_entries,
		{"Limited", "limited awd"},
	)

	local_lookup.assert_awaited_once()
	await_args = local_lookup.await_args
	assert await_args is not None
	assert await_args.args[4] == "Limited Sport Utility 4D"
	assert set(cache_entries) == {
		"2024 Hyundai IONIQ 5 Limited Sport Utility 4D"
	}


async def test_national_trim_link_is_preferred_and_saved_without_local_lookup(
	monkeypatch, caplog,
):
	caplog.set_level("INFO", logger="analysis.kbb")
	cache_entries = {}
	local_lookup = AsyncMock(return_value=(40_000, 48_000, 45_000, None, None))
	monkeypatch.setattr(
		"analysis.kbb.get_or_fetch_national_pricing",
		AsyncMock(return_value=([(
			"SE Standard Range Sport Utility 4D",
			"$43,175",
			"$22,400",
			"https://kbb.com/hyundai/ioniq-5/2024/",
			"/hyundai/ioniq-5/2024/se-standard-range-sport-utility-4d/",
			"2026-01-01T00:00:00",
		)], None)),
	)
	monkeypatch.setattr("analysis.kbb.get_or_fetch_local_pricing", local_lookup)

	await populate_pricing_for_year(
		AsyncMock(),
		"Hyundai",
		"IONIQ 5",
		"ioniq-5",
		"2024",
		cache_entries,
		set(),
	)

	local_lookup.assert_not_awaited()
	entry = cache_entries[
		"2024 Hyundai IONIQ 5 SE Standard Range Sport Utility 4D"
	]
	assert entry["local_source"] == (
		"https://kbb.com/hyundai/ioniq-5/2024/"
		"se-standard-range-sport-utility-4d/"
	)
	assert entry["local_timestamp"] is None
	assert (
		"SE Standard Range Sport Utility 4D: Local FPP=not checked | "
		"FMV=not checked"
	) in caplog.text


async def test_requested_trim_uses_national_table_link_first(monkeypatch):
	local_lookup = AsyncMock(return_value=(40_000, 48_000, 45_000, None, None))
	monkeypatch.setattr(
		"analysis.kbb.get_or_fetch_national_pricing",
		AsyncMock(return_value=([(
			"Disney100 Platinum Edition Sport Utility 4D",
			"$60,775",
			"$33,800",
			"https://kbb.com/hyundai/ioniq-5/2024/",
			"/hyundai/ioniq-5/2024/disney100-platinum-edition-sport-utility-4d/",
			"2026-01-01T00:00:00",
		)], None)),
	)
	monkeypatch.setattr("analysis.kbb.get_or_fetch_local_pricing", local_lookup)

	await populate_pricing_for_year(
		AsyncMock(),
		"Hyundai",
		"IONIQ 5",
		"ioniq-5",
		"2024",
		{},
		{"Disney100 Platinum Edition Sport Utility 4D"},
	)

	await_args = local_lookup.await_args
	assert await_args is not None
	assert await_args.kwargs["source_url"] == (
		"https://kbb.com/hyundai/ioniq-5/2024/"
		"disney100-platinum-edition-sport-utility-4d/"
	)


async def test_failed_used_vin_resolution_tries_national_table_link(monkeypatch):
	cache_entries = {}
	local_lookup = AsyncMock(
		return_value=(28_000, 32_000, 30_000, None, "direct-used-link")
	)
	monkeypatch.setattr(
		"analysis.kbb.get_or_fetch_national_pricing",
		AsyncMock(return_value=([(
			"Limited Sport Utility 4D",
			"$54,875",
			"$30,400",
			"https://kbb.com/hyundai/ioniq-5/2024/",
			"/hyundai/ioniq-5/2024/limited-sport-utility-4d/",
			"2026-01-01T00:00:00",
		)], None)),
	)
	monkeypatch.setattr("analysis.kbb.get_or_fetch_local_pricing", local_lookup)

	await populate_pricing_for_year(
		AsyncMock(),
		"Hyundai",
		"IONIQ 5",
		"ioniq-5",
		"2024",
		cache_entries,
		{"limited"},
		used_style_urls={"limited": None},
	)

	await_args = local_lookup.await_args
	assert await_args is not None
	assert await_args.kwargs["source_url"] == (
		"https://kbb.com/hyundai/ioniq-5/2024/"
		"limited-sport-utility-4d/"
	)
	assert await_args.kwargs["expect_used"] is True
	entry = cache_entries["2024 Hyundai IONIQ 5 Limited Sport Utility 4D"]
	assert entry["fpp_local"] == 30_000
	assert "skip_reason" not in entry


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

	result = await get_price_advisor_values(page)

	advisor.wait_for.assert_awaited_once_with(state="attached", timeout=30_000)
	advisor.get_attribute.assert_awaited_once_with("data", timeout=30_000)
	assert svg_page.goto.await_args.args[0] == "https://kbb.test/advisor.svg"
	assert result == (44_100, 46_800, 45_500)


async def test_price_advisor_reports_unavailable_without_inventing_values(caplog):
	page = MagicMock()
	advisor = MagicMock()
	advisor.first = advisor
	advisor.wait_for = AsyncMock()
	advisor.get_attribute = AsyncMock(
		return_value="https://kbb.test/advisor.svg?opaque=browser-context"
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

	with caplog.at_level("DEBUG", logger="analysis.kbb"):
		result = await get_price_advisor_values(page)

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
	)

	local_lookup.assert_awaited_once()
	await_args = local_lookup.await_args
	assert await_args is not None
	assert await_args.args[4] == "Sport"
	entry = cache_entries["2026 Honda Civic Sport"]
	assert entry["fpp_local"] == 26_100
	assert "skip_reason" not in entry
