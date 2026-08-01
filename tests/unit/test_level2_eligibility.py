import shutil
import uuid
import re

from pathlib import Path
from unittest.mock import Mock

from analysis import level2, reporting
from analysis.reporting import (
	build_level2_bins,
	create_report_filter_summary,
	display_dealer_location,
	display_listing_condition,
	get_images_for_listing,
	logo_data_uri,
	order_level2_ratings,
	summarize_level2_failures,
)
from jinja2 import Environment, FileSystemLoader
from utils.models import AnalysisContext, ListingContext, PricingAnchors


def test_level2_uses_one_local_report_image(monkeypatch):
	test_root = Path("cache") / "test-report-images" / uuid.uuid4().hex
	try:
		monkeypatch.setattr(reporting, "DOC_PATH", test_root)
		image_dir = test_root / "2026 Toyota 4Runner" / "TESTVIN" / "images"
		image_dir.mkdir(parents=True)
		(image_dir / "report.jpg").write_bytes(b"report image")
		(image_dir / "unused.jpg").write_bytes(b"unused image")

		images = get_images_for_listing(
			{"title": "2026 Toyota 4Runner", "vin": "TESTVIN"}
		)

		assert images == ["data:image/jpeg;base64,cmVwb3J0IGltYWdl"]
	finally:
		shutil.rmtree(test_root)


def test_level2_uses_msrp_only_for_new_vehicle_fallback():
	pricing = PricingAnchors(msrp=30_000)
	used = ListingContext(
		listing_id="used", listing={"price": 25_000, "condition": "Used"},
		pricing=pricing,
	)
	new = ListingContext(
		listing_id="new", listing={"price": 25_000, "condition": "New"},
		pricing=pricing,
	)

	assert level2._price_assessment(used, []) is None
	narrative = []
	assert level2._price_assessment(new, narrative) is not None
	assert any("fallback benchmark" in message for message in narrative)
	assert any("MSRP was used as the comparison value" in message for message in narrative)


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
		or ("Good", 0, 0, 0.0, {"listing_price": 25_000}),
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
			None,
			[
				"Price evidence available.",
				"A vehicle-history report was not collected, so risk and the final Level 2 rating are unavailable.",
			],
			{"listing_price": 25_000},
		)
	]
	assert render_args[5] == [
		(unmapped_listing, "The listing trim could not be mapped to compatible KBB pricing.")
	]


async def test_level2_rates_new_vehicle_without_history_report(monkeypatch):
	listing = {
		"id": "new-no-report",
		"vin": "NEWVIN",
		"title": "2026 Test Vehicle",
		"condition": "New",
		"price": 25_000,
	}
	ctx = AnalysisContext(make="Test", model="Vehicle")
	context = ListingContext(listing_id="new-no-report", listing=listing)
	ctx.listings = [context]

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
		or ("Good", 0, 0, 0.0, {"marker_pct": 25.0}),
	)
	monkeypatch.setattr(level2, "render_level2_pdf", fake_render)

	await level2.start_level2_analysis({}, [listing], "unused.json")

	assert len(render_args[3]) == 1
	assessed, _, risk, narrative, pricing = render_args[3][0]
	assert assessed == listing
	assert risk == 0
	assert context.risk_score == 0
	assert pricing["risk_summary"] == "none identified"
	assert "New vehicles do not require a vehicle history report." in narrative
	assert "Warranty active: ~36 months, ~36,000 miles remaining." in narrative
	assert "New vehicles do not require a vehicle history report" not in pricing["detail_scores"]
	assert not any("combined score changes" in line.casefold() for line in narrative)
	assert render_args[4] == []


async def test_level2_cannot_analyze_used_vehicle_without_mileage(monkeypatch):
	listing = {
		"id": "used-no-mileage",
		"vin": "2HGFE4F8XSH354866",
		"title": "2025 Honda Civic Sport",
		"condition": "Used",
		"mileage": None,
		"price": 29_436,
	}
	ctx = AnalysisContext(make="Honda", model="Civic")
	ctx.listings = [ListingContext(
		listing_id="used-no-mileage",
		listing=listing,
	)]

	async def fake_prepare(*_args, **_kwargs):
		return ctx

	render_args: tuple = ()

	async def fake_render(*args):
		nonlocal render_args
		render_args = args

	price_assessment = Mock()
	monkeypatch.setattr(level2, "prepare_level2_analysis", fake_prepare)
	monkeypatch.setattr(level2, "_price_assessment", price_assessment)
	monkeypatch.setattr(level2, "render_level2_pdf", fake_render)

	await level2.start_level2_analysis({}, [listing], "unused.json")

	price_assessment.assert_not_called()
	assert render_args[3] == []
	assert render_args[4] == []
	assert render_args[5] == [(listing, "Mileage not available.")]


async def test_level2_uses_existing_history_report_for_new_vehicle(monkeypatch):
	report = Path("cache") / f"test-new-carfax-{uuid.uuid4().hex}.html"
	report.parent.mkdir(parents=True, exist_ok=True)
	report.write_text("saved report", encoding="utf-8")
	listing = {
		"id": "new-with-report",
		"vin": "NEWVIN",
		"title": "2026 Test Vehicle",
		"condition": "New",
		"price": 25_000,
	}
	ctx = AnalysisContext(make="Test", model="Vehicle")
	ctx.listings = [ListingContext(
		listing_id="new-with-report",
		listing=listing,
		report_path=str(report),
	)]

	async def fake_prepare(*_args, **_kwargs):
		return ctx

	render_args: tuple = ()

	async def fake_render(*args):
		nonlocal render_args
		render_args = args

	parser = Mock(return_value=object())
	monkeypatch.setattr(level2, "prepare_level2_analysis", fake_prepare)
	monkeypatch.setattr(
		level2,
		"_price_assessment",
		lambda _lc, narrative: narrative.append("Price evidence available.")
		or ("Good", 0, 0, 0.0, {"marker_pct": 25.0}),
	)
	monkeypatch.setattr(level2, "get_carfax_data", parser)
	monkeypatch.setattr(level2, "score_title_status", lambda _carfax: 3.0)
	monkeypatch.setattr(level2, "score_mileage_use", lambda *_args: 0.0)
	monkeypatch.setattr(level2, "score_warranty_status", lambda *_args: 0.0)
	monkeypatch.setattr(level2, "_risk_summary", lambda *_args: "reported history")
	monkeypatch.setattr(level2, "render_level2_pdf", fake_render)

	await level2.start_level2_analysis({}, [listing], "unused.json")

	parser.assert_called_once_with(report)
	assert render_args[3][0][2] == 3
	assert render_args[3][0][4]["risk_summary"] == "reported history"
	assert not any(
		"New vehicles do not require a vehicle history report" in line
		for line in render_args[3][0][3]
	)
	report.unlink()


async def test_level2_uses_available_national_fpp_without_fmv(monkeypatch):
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
	assert len(render_args[4]) == 1
	assessed_listing, _, risk, narrative, pricing = render_args[4][0]
	assert assessed_listing == listing
	assert risk is None
	assert any(
		"National FPP was used as the comparison value" in message
		for message in narrative
	)
	assert narrative[1].startswith("Listing price is")
	assert pricing["listing_price"] == listing["price"]
	assert render_args[5] == []


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
		(missing_price, "Dealer has not set a listing price."),
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
	assert '<span>Unable to analyze</span><strong>2</strong>' in html
	assert "Listings not evaluated" in html
	assert "Unable to Evaluate" not in html
	assert html.index("Listings not evaluated") < html.index("<main")
	assert "HIDDENVIN" not in html


def test_report_hides_zero_unevaluated_sentence():
	template = Environment(loader=FileSystemLoader("templates")).get_template(
		"level2.html"
	)
	empty_bins = {
		name: [] for name in ("Great", "Good", "Fair", "Poor", "Bad")
	}
	html = template.render(
		make="Toyota",
		model="4Runner",
		report_title="Level 2",
		generated_at="today",
		summary="New listings",
		total_count=100,
		full_count=100,
		price_only=[],
		information_only=[],
		information_summary=[],
		rating_bins=empty_bins,
		great_bin=[],
		good_bin=[],
		fair_bin=[],
		poor_count=0,
		bad_count=0,
		all_images={},
	)

	assert "The remaining" not in html
	assert "could not be evaluated for the following reasons" not in html


def test_report_renders_price_only_row_without_deal_score():
	template = Environment(loader=FileSystemLoader("templates")).get_template(
		"level2.html"
	)
	listing = {
		"title": "2025 Toyota Prius LE",
		"display_title": "2025 Toyota Prius Plug-in Hybrid LE",
		"seller": {"name": "Test dealer", "location": "Denver, CO"},
		"vin": "TESTVIN",
		"mileage": 10_000,
		"price": 25_000,
		"condition": "Used",
		"dealer_listing": "https://dealer.test/listing",
		"visor_listing": "https://visor.test/listing",
	}
	pricing = {
		"great_end_pct": 20,
		"good_end_pct": 40,
		"fair_end_pct": 60,
		"poor_end_pct": 80,
		"marker_pct": 50,
		"great_high": 20_000,
		"good_high": 23_000,
		"fair_high": 26_000,
		"poor_high": 29_000,
		"kbb_url": "https://kbb.test/valuation",
		"detail_scores": {
			"Listing price is 22.8% above the fair-price midpoint.": "+0 score",
			"Vehicle has been driven significantly less than expected for its age (-82.7%).": "+6 score",
			"Warranty active: ~12 months, ~10,000 miles remaining.": "+4 score",
		},
		"risk_summary": "none identified",
		"risk_penalty": 0.0,
	}
	empty_bins = {
		name: [] for name in ("Great", "Good", "Fair", "Poor", "Bad")
	}

	html = template.render(
		make="Toyota",
		model="Prius",
		generated_at="today",
		summary="Used listings",
		total_count=1,
		full_count=0,
		price_only=[(
			listing,
			"Good",
			None,
			[
				"Listing price is 22.8% above the fair-price midpoint.",
				"Vehicle has been driven significantly less than expected for its age (-82.7%).",
				"Warranty active: ~12 months, ~10,000 miles remaining.",
			],
			pricing,
		)],
		information_summary=[],
		all_ratings=[],
		all_images={},
		display_dealer_location=display_dealer_location,
		display_listing_condition=display_listing_condition,
	)

	assert "Price-only ratings" in html
	assert "2025 Toyota Prius Plug-in Hybrid LE" in html
	assert ">2025 Toyota Prius LE<" not in html
	assert '<div class="deal-score">' not in html
	assert '<span class="deal-rating">Good</span>' in html
	assert '>Dealer listing</a>' in html
	assert '>Visor listing</a>' in html
	assert '>KBB valuation</a>' in html
	assert '<section class="snapshot">' in html
	assert '<span>Requested listings</span><strong>1</strong>' in html
	assert '<span>Risk-adjusted</span><strong>0</strong>' in html
	assert '<span>Price-only</span><strong>1</strong>' in html
	assert '<span>Unable to analyze</span><strong>0</strong>' in html
	assert re.search(
		r'<span class="listing-fact"\s*>\s*<strong>\s*\$25,000\s*</strong>\s*</span\s*>',
		html,
	)
	assert re.search(
		r'<span class="listing-fact"\s*>\s*Miles:\s*<strong>\s*10,000\s*</strong>\s*</span\s*>',
		html,
	)
	assert "Risk Score:" not in html
	assert "VIN: <strong>TESTVIN" not in html
	assert "Dealer: <strong>Test dealer" not in html
	assert html.index("Miles:") < html.index("Dealer listing</a>") < html.index('class="price-position"')
	assert '>Rating details<' not in html
	assert 'class="listing-notes"' not in html
	assert 'class="listing-layout price-only-layout"' in html
	assert "22.8% above" not in html
	assert "significantly less" not in html


def test_report_renders_missing_mileage_as_zero():
	template = Environment(loader=FileSystemLoader("templates")).get_template(
		"level2.html"
	)
	listing = {
		"title": "2026 Toyota 4Runner SR5",
		"seller": {"name": "Test dealer", "location": "Denver, CO"},
		"vin": "MISSINGMILEAGEVIN",
		"mileage": None,
		"price": 52_000,
		"condition": "New",
	}
	pricing = {
		"great_end_pct": 20,
		"good_end_pct": 40,
		"fair_end_pct": 60,
		"poor_end_pct": 80,
		"marker_pct": 50,
		"great_high": 48_000,
		"good_high": 50_000,
		"fair_high": 54_000,
		"poor_high": 56_000,
	}

	html = template.render(
		make="Toyota",
		model="4Runner",
		generated_at="today",
		summary="New listings",
		total_count=1,
		full_count=0,
		price_only=[(listing, "Fair", None, ["Price-only rating."], pricing)],
		information_summary=[],
		all_ratings=[],
		all_images={},
		display_dealer_location=display_dealer_location,
		display_listing_condition=display_listing_condition,
	)

	assert "Miles: <strong>0</strong>" in html


def test_price_only_listings_are_not_repeated_as_failure_reasons():
	summary = summarize_level2_failures(
		[({"id": "three"}, "Dealer has not set a listing price.")],
	)

	assert summary == [("Dealer has not set a listing price.", 1)]


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
	assert narrative[0].startswith("Local FPP was used as the comparison value")
	assert "Listing price is 7.4% below the fair-price midpoint." in narrative


def test_price_assessment_accepts_local_pricing_without_national_fpp():
	lc = ListingContext(
		listing_id="f150-xlt",
		listing={"price": 45_000, "condition": "New"},
		pricing=PricingAnchors(
			fpp_natl=None,
			fpp_local=44_880,
			fmr_low=40_880,
			fmr_high=48_980,
			source_local=(
				"https://kbb.com/ford/f150-supercrew-cab/2025/"
				"xlt-pickup-4d-5-1-2-ft/"
			),
		),
	)
	narrative = []

	assessment = level2._price_assessment(lc, narrative)

	assert assessment is not None
	visual = assessment[4]
	assert (
		visual["scale_low"]
		< visual["great_high"]
		< visual["good_high"]
		< visual["fair_high"]
		< visual["poor_high"]
		< visual["scale_high"]
	)
	assert visual.get("kbb_url") == lc.pricing.source_local
	assert narrative[0].startswith("Local FPP was used as the comparison value")


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
	icons = reporting.load_level2_icons(Path("img/icons/lucide"))
	assert {"circle-dollar-sign", "circle-gauge", "file-text", "shield-alert", "shield-check"} <= icons.keys()


def test_level2_template_keeps_jinja_out_of_inline_css():
	template = Path("templates/level2.html").read_text(encoding="utf-8")

	assert "style=" not in template
	assert "data-left-pct=" in template
	assert "data-width-pct=" in template
	assert "class=\"deal-score\"" in template


def test_price_only_ratings_start_on_a_new_page():
	stylesheet = Path("templates/level2.css").read_text(encoding="utf-8")

	assert "main.deal-bin + section.deal-bin" in stylesheet
	assert "break-before: page" in stylesheet
	assert "page-break-before: always" in stylesheet


def test_completed_ratings_have_counted_section_heading():
	template = Path("templates/level2.html").read_text(encoding="utf-8")

	assert "Completed Risk-Adjusted Rating ({{ full_count }})" in template
	assert "{% if all_ratings %}" in template


def test_level2_bins_sort_by_global_deal_score():
	ratings = [
		({"price": 20000}, "Fair", 0, [], {"deal_score": 20}),
		({"price": 21000}, "Fair", 1, [], {"deal_score": 80}),
		({"price": 19000}, "Fair", 0, [], {"deal_score": 50}),
	]

	bins = build_level2_bins(ratings)

	assert [rating[4]["deal_score"] for rating in bins["Fair"]] == [80, 50, 20]


def test_price_only_ratings_are_ordered_by_gauge_position():
	ratings = [
		({"id": "bad"}, "Bad", None, [], {"marker_pct": 95.0}),
		({"id": "fair"}, "Fair", None, [], {"marker_pct": 52.0}),
		({"id": "great"}, "Great", None, [], {"marker_pct": 8.0}),
		({"id": "poor"}, "Poor", None, [], {"marker_pct": 74.0}),
		({"id": "good"}, "Good", None, [], {"marker_pct": 27.0}),
	]

	ordered = reporting.order_price_only_ratings(ratings)

	assert [rating[4]["marker_pct"] for rating in ordered] == [
		8.0, 27.0, 52.0, 74.0, 95.0,
	]
