"""Deterministic confidence indicators for aggregate Level 1 markets."""

from collections.abc import Callable

from analysis.level1_kbb import Level1KBBResult
from analysis.level1_models import ConfidenceLevel, MarketConfidence
from visor_api.level1_service import Level1FacetCollection, Level1TrimFacetBucket
from visor_api.models import FacetStats


HIGH_MISSING_RATE = 0.20
def calculate_market_confidence(
	facets: Level1FacetCollection,
	kbb: Level1KBBResult,
) -> MarketConfidence:
	"""Calculate transparent aggregate confidence from verified source metrics."""
	buckets = [bucket for year in facets.years for bucket in year.trims]
	minimum_count = max(
		(response.response.meta.minimum_metric_count for response in facets.responses),
		default=1,
	)
	price_supported = _supported_count(
		buckets, lambda bucket: bucket.active_price_stats, minimum_count
	)
	active_days_supported = _supported_count(
		buckets, lambda bucket: bucket.active_days_on_market_stats, minimum_count
	)
	recent_sales_supported = sum(
		1 for bucket in buckets
		if (bucket.recently_sold_inventory_count or 0) >= minimum_count
	)
	price_missing = _missing_rate(buckets, lambda bucket: bucket.active_price_stats)
	mileage_missing = _missing_rate(buckets, lambda bucket: bucket.active_mileage_stats)
	active_days_missing = _missing_rate(
		buckets, lambda bucket: bucket.active_days_on_market_stats
	)
	recent_sales_missing = None
	price_dispersion = _maximum_cv(buckets, lambda bucket: bucket.active_price_stats)
	mileage_dispersion = _maximum_cv(
		buckets, lambda bucket: bucket.active_mileage_stats
	)
	mapped = {(match.year, match.visor_trim.casefold()) for match in kbb.matches}
	kbb_rate = len(mapped) / len(buckets) if buckets else None

	limitations = []
	if price_supported < len(buckets):
		limitations.append("price_samples_below_api_minimum")
	if active_days_supported < len(buckets):
		limitations.append("active_days_samples_below_api_minimum")
	for name, rate in (
		("price", price_missing),
		("mileage", mileage_missing),
		("active_days", active_days_missing),
		("recent_sales", recent_sales_missing),
	):
		if rate is not None and rate > HIGH_MISSING_RATE:
			limitations.append(f"{name}_missing_rate_above_20_percent")
	if kbb_rate is not None and kbb_rate < 1:
		limitations.append("kbb_mapping_incomplete")

	if not buckets or price_supported == 0 or active_days_supported == 0:
		level = ConfidenceLevel.LOW
	elif not mapped:
		level = ConfidenceLevel.LOW
	elif limitations:
		level = ConfidenceLevel.MODERATE
	else:
		level = ConfidenceLevel.HIGH

	return MarketConfidence(
		level=level,
		minimum_metric_count=minimum_count,
		trim_bucket_count=len(buckets),
		price_supported_bucket_count=price_supported,
		active_days_supported_bucket_count=active_days_supported,
		recent_sales_supported_bucket_count=recent_sales_supported,
		price_missing_rate=price_missing,
		mileage_missing_rate=mileage_missing,
		active_days_missing_rate=active_days_missing,
		recent_sales_missing_rate=recent_sales_missing,
		maximum_price_coefficient_of_variation=price_dispersion,
		maximum_mileage_coefficient_of_variation=mileage_dispersion,
		kbb_mapping_rate=kbb_rate,
		limitations=tuple(limitations),
	)


def _supported_count(
	buckets: list[Level1TrimFacetBucket],
	stats: Callable[[Level1TrimFacetBucket], FacetStats | None],
	minimum_count: int,
) -> int:
	return sum(
		1 for bucket in buckets
		if (metric := stats(bucket)) is not None and metric.count >= minimum_count
	)


def _missing_rate(
	buckets: list[Level1TrimFacetBucket],
	stats: Callable[[Level1TrimFacetBucket], FacetStats | None],
) -> float | None:
	metrics = [metric for bucket in buckets if (metric := stats(bucket)) is not None]
	total = sum(metric.count + metric.missing for metric in metrics)
	return sum(metric.missing for metric in metrics) / total if total else None


def _maximum_cv(
	buckets: list[Level1TrimFacetBucket],
	stats: Callable[[Level1TrimFacetBucket], FacetStats | None],
) -> float | None:
	values = [
		metric.stddev / abs(metric.mean)
		for bucket in buckets
		if (metric := stats(bucket)) is not None and metric.mean != 0
	]
	return max(values) if values else None
