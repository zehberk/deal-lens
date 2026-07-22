from analysis import level2
from analysis.reporting import build_level2_bins
from utils.models import AnalysisContext, ListingContext


async def test_level2_keeps_price_only_and_unmapped_listings(monkeypatch):
	price_only_listing = {
		"id": "price-only",
		"vin": "VIN1",
		"title": "Price-only vehicle",
		"price": 25000,
	}
	unmapped_listing = {
		"id": "unmapped",
		"vin": "VIN2",
		"title": "Unmapped vehicle",
		"price": 26000,
	}
	ctx = AnalysisContext(make="Subaru", model="Forester")
	ctx.listings = [ListingContext(listing_id="price-only", listing=price_only_listing)]
	ctx.skipped_listings = [unmapped_listing]

	async def fake_prepare(*_args, **_kwargs):
		return ctx

	async def fake_render(*args):
		fake_render.args = args

	monkeypatch.setattr(level2, "prepare_level2_analysis", fake_prepare)
	monkeypatch.setattr(
		level2,
		"_price_assessment",
		lambda _lc, narrative: narrative.append("Price evidence available.")
		or ("Good", 0, 0, 0.0),
	)
	monkeypatch.setattr(level2, "render_level2_pdf", fake_render)

	await level2.start_level2_analysis(
		{"vehicle": {"make": "Subaru", "model": "Forester"}},
		[price_only_listing, unmapped_listing],
		"unused.json",
	)

	assert fake_render.args[3] == []
	assert fake_render.args[4] == [
		(
			price_only_listing,
			"Good",
			[
				"Price evidence available.",
				"A vehicle-history report was not collected, so risk and the final Level 2 rating are unavailable.",
			],
		)
	]
	assert fake_render.args[5] == [
		(unmapped_listing, "The listing trim could not be mapped to compatible KBB pricing.")
	]


def test_level2_bins_retain_unfavorable_complete_ratings():
	ratings = [
		({"id": "poor", "price": 20000}, "Poor", 2, []),
		({"id": "bad", "price": 21000}, "Bad", 3, []),
		({"id": "suspicious", "price": 10000}, "Suspicious", 7, []),
	]

	bins = build_level2_bins(ratings)

	assert bins["Poor"] == [ratings[0]]
	assert bins["Bad"] == [ratings[1]]
	assert bins["Suspicious"] == [ratings[2]]
