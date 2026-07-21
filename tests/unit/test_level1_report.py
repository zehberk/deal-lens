from dataclasses import replace

from analysis.level1_kbb import Level1KBBMatch, Level1KBBResult
from analysis.level1_models import (
	ActiveInventoryMetrics,
	ConfidenceLevel,
	MarketCohort,
	MarketConfidence,
	MarketQueryProvenance,
	MarketSearchScope,
	MarketSnapshot,
	MetricSummary,
	RecentlySoldMetrics,
	YearTrimSummary,
)
from analysis.level1_report import render_level1_html
from utils.models import TrimValuation


def metric(median, count, missing=0, stddev=1_000):
	return MetricSummary(
		median=median,
		sample_count=count,
		missing_count=missing,
		minimum=median * 0.8,
		maximum=median * 1.2,
		standard_deviation=stddev,
	)


def report_data():
	rows = (
		YearTrimSummary(
			year=2024,
			trim="LX",
			active=ActiveInventoryMetrics(
				inventory_count=42,
				asking_price=metric(24_900, 40, 2),
				mileage=metric(18_200, 39, 3),
				listing_age_days=metric(31, 42),
			),
			recently_sold=RecentlySoldMetrics(
				inventory_count=14,
				time_to_sale_days=metric(24, 14),
			),
		),
		YearTrimSummary(
			year=2024,
			trim="Sport",
			active=ActiveInventoryMetrics(
				inventory_count=58,
				asking_price=metric(27_500, 57, 1),
				mileage=metric(15_800, 55, 3),
				listing_age_days=metric(27, 58),
			),
			recently_sold=RecentlySoldMetrics(
				inventory_count=19,
				time_to_sale_days=metric(20, 19),
			),
		),
	)
	snapshot = MarketSnapshot(
		scope=MarketSearchScope(
			make="Honda",
			model="Civic",
			years=(2024,),
			conditions=("used",),
			geography={
				"postal_code": "80202",
				"distance_type": "radius",
				"distance_value": "100",
				"distance_unit": "mi",
			},
		),
		active=ActiveInventoryMetrics(
			inventory_count=100,
			asking_price=metric(26_400, 97, 3),
			mileage=metric(16_900, 94, 6),
			listing_age_days=metric(29, 100),
		),
		recently_sold=RecentlySoldMetrics(
			inventory_count=33,
			time_to_sale_days=metric(22, 33),
		),
		year_trim_summaries=rows,
		queries=tuple(
			MarketQueryProvenance(
				cohort=cohort,
				metric=metric_name,
				endpoint="/v1/facets",
				filters={"year": ("2024",)},
				minimum_metric_count=5,
				retrieved_at="2026-07-20T15:00:00+00:00",
				request_url=(
					"https://api.visor.vin/v1/facets?make=Honda&model=Civic&"
					"postal_code=80202&radius=100&year=2024&facets=trim&sort=-count&"
					f"metric={metric_name}&facet_value_limit=100"
				),
			)
			for cohort, metric_name in (
				(MarketCohort.ACTIVE, "price.median"),
				(MarketCohort.ACTIVE, "days_on_market.median"),
				(MarketCohort.RECENTLY_SOLD, "count"),
			)
		),
		confidence=MarketConfidence(
			level=ConfidenceLevel.HIGH,
			minimum_metric_count=5,
			trim_bucket_count=2,
			price_supported_bucket_count=2,
			active_days_supported_bucket_count=2,
			recent_sales_supported_bucket_count=2,
			price_missing_rate=0.03,
			mileage_missing_rate=0.06,
			active_days_missing_rate=0,
			recent_sales_missing_rate=0,
			maximum_price_coefficient_of_variation=0.12,
			maximum_mileage_coefficient_of_variation=0.2,
			kbb_mapping_rate=1,
		),
		generated_at="2026-07-20T15:02:00+00:00",
	)
	matches = tuple(
		Level1KBBMatch(
			year=2024,
			visor_trim=trim,
			kbb_trim=trim,
			valuation=TrimValuation(
				model="Civic",
				kbb_trim=f"2024 Honda Civic {trim}",
				msrp=27_000,
				fpp_natl=25_500,
				fmr_low=22_000,
				fmr_high=26_000,
				fpp_local=25_200,
				fmv=fmv,
				natl_source="https://www.kbb.com/honda/civic/2024/",
				local_source=(
					f"https://www.kbb.com/honda/civic/2024/{trim.lower()}/?zip=80202"
				),
			),
		)
		for trim, fmv in (("LX", 23_800), ("Sport", 26_200))
	)
	return snapshot, Level1KBBResult(matches=matches, failures=())


def test_report_contains_required_aggregate_sections_only():
	snapshot, kbb = report_data()
	html = render_level1_html(snapshot, kbb)

	for text in (
		"Overall market snapshot",
		"Year and trim comparison",
		"Market observations",
		"Confidence and limitations",
		"Sources and retrieval",
		"Median of trim active-age medians",
		"Median of trim asking-price medians",
		"Recent sales (last 14d)",
		"This report covers used 2024 Honda Civics, within 100 mi radius of 80202.",
		"Market share",
		"KBB benchmark",
		"https://api.visor.vin/v1/facets?make=Honda",
	):
		assert text in html
	for forbidden in ("VIN", "Seller", "Deal rating", "Risk rating"):
		assert forbidden not in html


def test_report_condenses_nonconsecutive_years_and_formats_kilometers():
	snapshot, kbb = report_data()
	scope = replace(
		snapshot.scope,
		years=(2021, 2023, 2024, 2026),
		geography={
			"postal_code": "V6B 1A1",
			"distance_type": "radius",
			"distance_value": "75",
			"distance_unit": "km",
		},
	)
	html = render_level1_html(replace(snapshot, scope=scope), kbb)

	assert "used 2021, 2023-2024, 2026 Honda Civics" in html
	assert "within 75 km radius of V6B 1A1" in html
