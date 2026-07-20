import pytest

from analysis.level1_models import (
	ActiveInventoryMetrics,
	MarketCohort,
	MarketQueryProvenance,
	MarketSearchScope,
	MarketSnapshot,
	MetricSummary,
	RecentlySoldMetrics,
	YearTrimSummary,
)


def metric(median=25_000, *, samples=8, missing=2):
	return MetricSummary(
		median=median,
		sample_count=samples,
		missing_count=missing,
		minimum=20_000,
		maximum=30_000,
		standard_deviation=2_500,
	)


def active(count=10):
	return ActiveInventoryMetrics(
		inventory_count=count,
		asking_price=metric(),
		mileage=metric(30_000),
		listing_age_days=metric(42),
	)


def sold(count=6):
	return RecentlySoldMetrics(
		inventory_count=count,
		time_to_sale_days=metric(31, samples=6, missing=0),
	)


def snapshot():
	scope = MarketSearchScope(
		make="Honda",
		model="Civic",
		years=(2024,),
		conditions=("used",),
		geography={"postal_code": "80202", "radius": "100"},
		selected_trims=("Sport",),
	)
	return MarketSnapshot(
		scope=scope,
		active=active(),
		recently_sold=sold(),
		year_trim_summaries=(
			YearTrimSummary(
				year=2024,
				trim="Sport",
				active=active(),
				recently_sold=sold(),
			),
		),
		queries=(
			MarketQueryProvenance(
				cohort=MarketCohort.ACTIVE,
				metric="price.median",
				endpoint="/v1/facets",
				filters={"year": ("2024",), "inventory_type": ("used",)},
				minimum_metric_count=5,
				retrieved_at="2026-07-20T12:00:00+00:00",
			),
		),
		generated_at="2026-07-20T12:01:00+00:00",
	)


def test_snapshot_contract_round_trips_as_json_compatible_data():
	original = snapshot()

	serialized = original.to_dict()
	restored = MarketSnapshot.from_dict(serialized)

	assert restored == original
	assert serialized["queries"][0]["cohort"] == "active"
	assert serialized["scope"]["years"] == [2024]
	assert serialized["queries"][0]["filters"]["year"] == ["2024"]


def test_active_listing_age_and_sold_time_to_sale_are_distinct_fields():
	result = snapshot().to_dict()

	assert "listing_age_days" in result["active"]
	assert "time_to_sale_days" not in result["active"]
	assert "time_to_sale_days" in result["recently_sold"]
	assert "listing_age_days" not in result["recently_sold"]


def test_unavailable_metric_requires_an_explicit_reason():
	with pytest.raises(ValueError, match="missing_reason"):
		MetricSummary(median=None, sample_count=0, missing_count=4)

	result = MetricSummary(
		median=None,
		sample_count=0,
		missing_count=4,
		missing_reason="below_api_minimum_metric_count",
	)

	assert result.missing_reason == "below_api_minimum_metric_count"


def test_counts_and_aware_retrieval_timestamps_are_validated():
	with pytest.raises(ValueError, match="non-negative integer"):
		RecentlySoldMetrics(inventory_count=-1, time_to_sale_days=metric())
	with pytest.raises(ValueError, match="UTC offset"):
		MarketQueryProvenance(
			cohort=MarketCohort.ACTIVE,
			metric="price.median",
			endpoint="/v1/facets",
			filters={},
			minimum_metric_count=5,
			retrieved_at="2026-07-20T12:00:00",
		)


def test_year_trim_summaries_must_be_unique_and_within_scope():
	base = snapshot()
	duplicate = base.year_trim_summaries[0]

	with pytest.raises(ValueError, match="unique"):
		MarketSnapshot(
			scope=base.scope,
			active=base.active,
			recently_sold=base.recently_sold,
			year_trim_summaries=(duplicate, duplicate),
			queries=base.queries,
			generated_at=base.generated_at,
		)
