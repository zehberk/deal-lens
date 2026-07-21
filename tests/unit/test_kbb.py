from unittest.mock import AsyncMock

from playwright.async_api import TimeoutError

from analysis.kbb import get_or_fetch_local_pricing


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
