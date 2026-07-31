import uuid

from pathlib import Path
from unittest.mock import AsyncMock, Mock

from analysis import level3
from utils.models import AnalysisContext, ListingContext


async def test_level3_rates_new_vehicle_without_history_report(monkeypatch):
	listing = {
		"id": "new-no-report",
		"condition": "New",
		"price": 25_000,
	}
	context = ListingContext(listing_id="new-no-report", listing=listing)
	ctx = AnalysisContext(make="Test", model="Vehicle")
	ctx.listings = [context]
	ctx.cache_entries = {
		"": {"fpp_natl": 26_000, "fpp_local": 0, "fmr_high": 0, "fmv": 0, "msrp": 0}
	}

	monkeypatch.setattr(level3, "prepare_level3_analysis", AsyncMock(return_value=ctx))
	monkeypatch.setattr(level3, "get_report_dir", lambda _listing: None)
	monkeypatch.setattr(
		level3,
		"get_carfax_data",
		lambda _report: (_ for _ in ()).throw(AssertionError("CARFAX should not be parsed")),
	)
	adjust = Mock(return_value="Good")
	monkeypatch.setattr(level3, "adjust_deal_for_risk", adjust)

	await level3.start_level3_analysis({}, [listing], "unused.json")

	adjust.assert_called_once()
	assert adjust.call_args.args[1] == 0
	assert any(
		"New vehicles do not require a vehicle history report" in line
		for line in adjust.call_args.args[2]
	)


async def test_level3_uses_existing_history_report_for_new_vehicle(monkeypatch):
	report = Path("cache") / f"test-new-carfax-{uuid.uuid4().hex}.html"
	report.parent.mkdir(parents=True, exist_ok=True)
	report.write_text("saved report", encoding="utf-8")
	listing = {
		"id": "new-with-report",
		"condition": "New",
		"price": 25_000,
	}
	context = ListingContext(listing_id="new-with-report", listing=listing)
	ctx = AnalysisContext(make="Test", model="Vehicle")
	ctx.listings = [context]
	ctx.cache_entries = {
		"": {"fpp_natl": 26_000, "fpp_local": 0, "fmr_high": 0, "fmv": 0, "msrp": 0}
	}

	monkeypatch.setattr(level3, "prepare_level3_analysis", AsyncMock(return_value=ctx))
	monkeypatch.setattr(level3, "get_report_dir", lambda _listing: report)
	parser = Mock(return_value=object())
	monkeypatch.setattr(level3, "get_carfax_data", parser)
	monkeypatch.setattr(level3, "rate_risk_level2", Mock(return_value=4))
	adjust = Mock(return_value="Good")
	monkeypatch.setattr(level3, "adjust_deal_for_risk", adjust)

	await level3.start_level3_analysis({}, [listing], "unused.json")

	parser.assert_called_once_with(report)
	assert adjust.call_args.args[1] == 4
	assert not any(
		"New vehicles do not require a vehicle history report" in line
		for line in adjust.call_args.args[2]
	)
	report.unlink()
