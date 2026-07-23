from pathlib import Path

from analysis import level2
from analysis.reporting import (
	build_level2_bins,
	create_report_filter_summary,
	display_dealer_location,
	display_listing_condition,
	logo_data_uri,
	summarize_level2_failures,
)
from jinja2 import Environment, FileSystemLoader
from utils.models import AnalysisContext, ListingContext, PricingAnchors


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

	render_args: tuple = ()

	async def fake_render(*args):
		nonlocal render_args
		render_args = args

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

	assert render_args[3] == []
	assert render_args[4] == [
		(
			price_only_listing,
			"Good",
			[
				"Price evidence available.",
				"A vehicle-history report was not collected, so risk and the final Level 2 rating are unavailable.",
			],
		)
	]
	assert render_args[5] == [
		(unmapped_listing, "The listing trim could not be mapped to compatible KBB pricing.")
	]


async def test_level2_records_missing_complete_kbb_pricing(monkeypatch):
	listing = {
		"id": "incomplete-kbb",
		"vin": "VIN3",
		"title": "Incomplete KBB vehicle",
		"price": 25_000,
	}
	ctx = AnalysisContext(make="Subaru", model="Forester")
	ctx.listings = [ListingContext(
		listing_id="incomplete-kbb",
		listing=listing,
		pricing=PricingAnchors(
			fpp_natl=26_000,
			fpp_local=None,
			fmv=24_000,
			fmr_high=28_000,
		),
	)]

	async def fake_prepare(*_args, **_kwargs):
		return ctx

	render_args: tuple = ()

	async def fake_render(*args):
		nonlocal render_args
		render_args = args

	monkeypatch.setattr(level2, "prepare_level2_analysis", fake_prepare)
	monkeypatch.setattr(level2, "render_level2_pdf", fake_render)

	await level2.start_level2_analysis(
		{"vehicle": {"make": "Subaru", "model": "Forester"}},
		[listing],
		"unused.json",
	)

	assert render_args[3] == []
	assert render_args[4] == []
	assert render_args[5] == [
		(listing, "Complete KBB pricing is unavailable for this configuration.")
	]


async def test_level2_records_missing_price_separately_from_kbb_mapping(monkeypatch):
	missing_price = {"id": "no-price", "vin": "VIN4", "title": "No price"}
	unmapped = {
		"id": "unmapped",
		"vin": "VIN5",
		"title": "Unmapped trim",
		"price": 26_000,
	}
	ctx = AnalysisContext(make="Subaru", model="Forester")
	ctx.skipped_listings = [missing_price, unmapped]

	async def fake_prepare(*_args, **_kwargs):
		return ctx

	render_args: tuple = ()

	async def fake_render(*args):
		nonlocal render_args
		render_args = args

	monkeypatch.setattr(level2, "prepare_level2_analysis", fake_prepare)
	monkeypatch.setattr(level2, "render_level2_pdf", fake_render)

	await level2.start_level2_analysis(
		{"vehicle": {"make": "Subaru", "model": "Forester"}},
		[missing_price, unmapped],
		"unused.json",
	)

	assert render_args[5] == [
		(missing_price, "Listing price is unavailable."),
		(unmapped, "The listing trim could not be mapped to compatible KBB pricing."),
	]


def test_level2_bins_retain_unfavorable_complete_ratings():
	ratings = [
		({"id": "poor", "price": 20000}, "Poor", 2, []),
		({"id": "bad", "price": 21000}, "Bad", 3, []),
		({"id": "great", "price": 10000}, "Great", 7, []),
	]

	bins = build_level2_bins(ratings)

	assert bins["Poor"] == [ratings[0]]
	assert bins["Bad"] == [ratings[1]]
	assert bins["Great"] == [ratings[2]]


def test_report_summary_accepts_single_condition_string():
	summary = create_report_filter_summary(
		{"filters": {"car_type": "used", "sort": "Newest"}}
	)

	assert "Used listings" in summary
	assert "New, Used, and Certified" not in summary


def test_report_summarizes_unevaluated_reasons_without_listing_rows():
	template = Environment(loader=FileSystemLoader("templates")).get_template(
		"level2.html"
	)
	empty_bins = {
		name: [] for name in ("Great", "Good", "Fair", "Poor", "Bad")
	}
	html = template.render(
		make="Subaru",
		model="Forester",
		report_title="Level 2",
		generated_at="today",
		summary="Used listings",
		total_count=2,
		full_count=0,
		price_only=[],
		information_only=[({"vin": "HIDDENVIN"}, "KBB pricing unavailable")],
		information_summary=[("KBB pricing unavailable", 2)],
		rating_bins=empty_bins,
		great_bin=[],
		good_bin=[],
		fair_bin=[],
		poor_count=0,
		bad_count=0,
		all_images={},
	)

	assert "KBB pricing unavailable <strong>(2)</strong>" in html
	assert "HIDDENVIN" not in html


def test_price_only_listings_are_grouped_with_other_failure_reasons():
	summary = summarize_level2_failures(
		[({"id": "one"}, "Good", []), ({"id": "two"}, "Fair", [])],
		[({"id": "three"}, "Listing price is unavailable.")],
	)

	assert summary == [
		("Listing price is unavailable.", 1),
		("Vehicle-history report unavailable.", 2),
	]


def test_price_assessment_provides_visual_range_without_redundant_bullets():
	lc = ListingContext(
		listing_id="one",
		listing={"price": 25000},
		pricing=PricingAnchors(
			fpp_natl=26000,
			fpp_local=25000,
			fmv=24000,
			fmr_high=28000,
		),
	)
	narrative = []

	assessment = level2._price_assessment(lc, narrative)

	assert assessment is not None
	visual = assessment[4]
	assert visual["fair_low"] < visual["fair_high"]
	assert (
		visual["great_high"]
		< visual["good_high"]
		< visual["fair_high"]
		< visual["poor_high"]
	)
	assert 0 <= visual["marker_pct"] <= 100
	assert not any("being listed at" in line for line in narrative)
	assert not any("Deal bins are set" in line for line in narrative)
	assert "Listing price is 7.4% below the fair-price midpoint." in narrative


def test_price_assessment_explains_percent_from_fair_midpoint():
	lc = ListingContext(
		listing_id="one",
		listing={"price": 27500},
		pricing=PricingAnchors(
			fpp_natl=26000,
			fpp_local=25000,
			fmv=24000,
			fmr_high=28000,
		),
	)
	narrative = []

	level2._price_assessment(lc, narrative)

	assert "Listing price is 1.9% above the fair-price midpoint." in narrative


def test_price_below_displayed_great_range_remains_great_and_caps_marker():
	lc = ListingContext(
		listing_id="one",
		listing={"price": 24000},
		pricing=PricingAnchors(
			fpp_natl=26000,
			fpp_local=25000,
			fmv=24000,
			fmr_high=28000,
		),
	)

	inside_great = level2._price_assessment(lc, [])
	lc.listing["price"] = 21999
	below_great = level2._price_assessment(lc, [])

	assert inside_great is not None and inside_great[0] == "Great"
	assert below_great is not None and below_great[0] == "Great"
	assert below_great[4]["marker_pct"] == 0


def test_level2_branding_and_dealer_location_helpers():
	assert display_dealer_location("Denver, CO 80202") == "Denver, CO"
	assert display_dealer_location("Denver, CO") == "Denver, CO"
	logo = logo_data_uri(Path("img/deallens-logo.svg"))
	assert logo is not None
	assert logo.startswith(
		"data:image/svg+xml;base64,"
	)
	assert display_listing_condition("Certified") == "CPO"
	assert display_listing_condition("Used") == "Used"
	assert display_listing_condition(None) == "Unknown"


def test_level2_template_keeps_jinja_out_of_inline_css():
	template = Path("templates/level2.html").read_text(encoding="utf-8")

	assert "style=" not in template
	assert "data-left-pct=" in template
	assert "data-width-pct=" in template
	assert "class=\"deal-score\"" in template


def test_level2_bins_sort_by_global_deal_score():
	ratings = [
		({"price": 20000}, "Fair", 0, [], {"deal_score": 20}),
		({"price": 21000}, "Fair", 1, [], {"deal_score": 80}),
		({"price": 19000}, "Fair", 0, [], {"deal_score": 50}),
	]

	bins = build_level2_bins(ratings)

	assert [rating[4]["deal_score"] for rating in bins["Fair"]] == [80, 50, 20]
