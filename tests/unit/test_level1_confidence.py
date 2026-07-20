from analysis.level1_confidence import calculate_market_confidence
from analysis.level1_kbb import Level1KBBFailure, Level1KBBMatch, Level1KBBResult
from analysis.level1_models import ConfidenceLevel, MarketCohort
from utils.models import TrimValuation
from visor_api import (
	FacetResponse,
	Level1FacetCollection,
	Level1FacetQuery,
	Level1TrimFacetBucket,
	Level1YearFacetResult,
	RetrievedLevel1Facet,
)
from visor_api.models import FacetStats


def stats(count=10, missing=0, mean=100, stddev=10):
	return FacetStats(
		min=1,
		max=200,
		count=count,
		missing=missing,
		mean=mean,
		median=100,
		stddev=stddev,
	)


def collection(*, sold_count=10, price_missing=0, price_stddev=10):
	bucket = Level1TrimFacetBucket(
		year=2024,
		trim="Sport",
		active_inventory_count=10,
		recently_sold_inventory_count=sold_count,
		active_price_median=25_000,
		active_days_on_market_median=20,
		recently_sold_days_on_market_median=15,
		active_price_stats=stats(missing=price_missing, stddev=price_stddev),
		active_mileage_stats=stats(),
		active_days_on_market_stats=stats(),
		recently_sold_days_on_market_stats=stats(count=sold_count),
	)
	response = FacetResponse.from_dict({
		"data": {"total": 10, "facets": {"trim": []}, "range_facets": {}, "stats": {}},
		"meta": {
			"facets": ["trim"],
			"metric": "price.median",
			"sort": "-count",
			"minimum_metric_count": 5,
		},
	})
	return Level1FacetCollection(
		years=(Level1YearFacetResult(
			year=2024,
			active_inventory_count=10,
			recently_sold_inventory_count=sold_count,
			trims=(bucket,),
		),),
		responses=(RetrievedLevel1Facet(
			query=Level1FacetQuery(
				year=2024,
				cohort=MarketCohort.ACTIVE,
				metric="price.median",
				filters={},
			),
			response=response,
			retrieved_at="2026-07-20T00:00:00+00:00",
		),),
	)


def kbb(mapped=True):
	valuation = TrimValuation(
		model="Civic",
		kbb_trim="2024 Honda Civic Sport",
		msrp=25_000,
		fpp_natl=22_000,
		fmr_low=20_000,
		fmr_high=24_000,
		fpp_local=22_500,
		fmv=18_000,
		natl_source="national",
		local_source="local",
	)
	return Level1KBBResult(
		matches=(Level1KBBMatch(
			year=2024,
			visor_trim="Sport",
			kbb_trim="Sport",
			valuation=valuation,
		),) if mapped else (),
		failures=() if mapped else (Level1KBBFailure(
			year=2024,
			visor_trim="Sport",
			reason="kbb_trim_not_found",
		),),
	)


def test_complete_supported_market_has_high_confidence():
	result = calculate_market_confidence(collection(), kbb())

	assert result.level is ConfidenceLevel.HIGH
	assert result.kbb_mapping_rate == 1
	assert result.limitations == ()


def test_missing_sparse_and_dispersed_data_reduce_confidence():
	result = calculate_market_confidence(
		collection(sold_count=2, price_missing=5, price_stddev=75),
		kbb(),
	)

	assert result.level is ConfidenceLevel.LOW
	assert "sparse_recent_sales" in result.limitations
	assert "price_missing_rate_above_20_percent" in result.limitations
	assert "high_price_dispersion" in result.limitations


def test_missing_kbb_mapping_is_explicit_and_low_confidence():
	result = calculate_market_confidence(collection(), kbb(mapped=False))

	assert result.level is ConfidenceLevel.LOW
	assert result.kbb_mapping_rate == 0
	assert result.limitations == ("kbb_mapping_incomplete",)
