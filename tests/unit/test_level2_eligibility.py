from pathlib import Path

from analysis import level2
from analysis.reporting import (
	build_level2_bins,
	create_report_filter_summary,
	display_dealer_location,
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
		name: [] for name in ("Great", "Good", "Fair", "Poor", "Bad", "Suspicious")
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
		sus_count=0,
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


def test_level2_branding_and_dealer_location_helpers():
	assert display_dealer_location("Denver, CO 80202") == "Denver, CO"
	assert display_dealer_location("Denver, CO") == "Denver, CO"
	assert logo_data_uri(Path("img/deallens-logo.svg")).startswith(
		"data:image/svg+xml;base64,"
	)
