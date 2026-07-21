"""Build the stable Level 1 snapshot from facet and KBB results."""

from datetime import datetime, timezone
from statistics import median

from analysis.level1_confidence import calculate_market_confidence
from analysis.level1_kbb import Level1KBBResult
from analysis.level1_models import (
	ActiveInventoryMetrics,
	MarketQueryProvenance,
	MarketSearchScope,
	MarketSnapshot,
	MetricSummary,
	RecentlySoldMetrics,
	YearTrimSummary,
)
from visor_api.level1_service import Level1FacetCollection, Level1TrimFacetBucket
from visor_api.models import FacetStats
from visor_api.query import VisorListingQuery


def build_market_snapshot(
	query: VisorListingQuery,
	facets: Level1FacetCollection,
	kbb: Level1KBBResult,
	*,
	generated_at: str | None = None,
) -> MarketSnapshot:
	"""Convert verified aggregate inputs into the reporting contract."""
	filters = query.market_filters()
	rows = tuple(
		_summary(bucket)
		for year in facets.years
		for bucket in year.trims
	)
	return MarketSnapshot(
		scope=MarketSearchScope(
			make=_single(filters, "make"),
			model=_single(filters, "model"),
			years=tuple(year.year for year in facets.years),
			conditions=_values(filters.get("inventory_type")),
			geography=_geography(filters),
			selected_trims=_values(filters.get("trim")),
			additional_filters={
				name: value
				for name, value in filters.items()
				if name not in {
					"make", "model", "year", "inventory_type", "postal_code",
					"radius", "state", "trim", "sold_within_days",
				}
			},
		),
		active=ActiveInventoryMetrics(
			inventory_count=sum(year.active_inventory_count for year in facets.years),
			asking_price=_aggregate_unavailable(rows, "asking_price"),
			mileage=_aggregate_unavailable(rows, "mileage"),
			listing_age_days=_aggregate_unavailable(rows, "listing_age_days"),
		),
		recently_sold=RecentlySoldMetrics(
			inventory_count=sum(year.recently_sold_inventory_count for year in facets.years),
			time_to_sale_days=_aggregate_unavailable(rows, "time_to_sale_days"),
		),
		year_trim_summaries=rows,
		queries=tuple(
			MarketQueryProvenance(
				cohort=item.query.cohort,
				metric=item.query.metric,
				endpoint="/v1/facets",
				filters={
					name: tuple(str(part) for part in value)
					if isinstance(value, (list, tuple)) else str(value)
					for name, value in item.query.filters.items()
				},
				minimum_metric_count=item.response.meta.minimum_metric_count,
				retrieved_at=item.retrieved_at,
				request_url=item.request_url or item.query.request_url(),
			)
			for item in facets.responses
		),
		confidence=calculate_market_confidence(facets, kbb),
		generated_at=generated_at or datetime.now(timezone.utc).isoformat(),
	)


def _summary(bucket: Level1TrimFacetBucket) -> YearTrimSummary:
	return YearTrimSummary(
		year=bucket.year,
		trim=bucket.trim,
		active=ActiveInventoryMetrics(
			inventory_count=bucket.active_inventory_count or 0,
			asking_price=_metric(
				bucket.active_price_median,
				bucket.active_price_stats,
				bucket.active_price_missing_reason,
			),
			mileage=_metric(None, bucket.active_mileage_stats, "mileage_not_returned"),
			listing_age_days=_metric(
				bucket.active_days_on_market_median,
				bucket.active_days_on_market_stats,
				bucket.active_days_on_market_missing_reason,
			),
		),
		recently_sold=RecentlySoldMetrics(
			inventory_count=bucket.recently_sold_inventory_count or 0,
			time_to_sale_days=_metric(
				bucket.recently_sold_days_on_market_median,
				bucket.recently_sold_days_on_market_stats,
				bucket.recently_sold_days_on_market_missing_reason,
			),
		),
	)


def _metric(value, stats: FacetStats | None, reason: str | None) -> MetricSummary:
	median = stats.median if value is None and stats is not None else value
	if stats is None or stats.count == 0:
		median = None
	return MetricSummary(
		median=median,
		sample_count=stats.count if stats else 0,
		missing_count=stats.missing if stats else 0,
		minimum=stats.min if stats else None,
		maximum=stats.max if stats else None,
		standard_deviation=stats.stddev if stats else None,
		missing_reason=(reason or "metric_not_returned") if median is None else None,
	)


def _aggregate_unavailable(
	rows: tuple[YearTrimSummary, ...],
	name: str,
) -> MetricSummary:
	metrics = []
	for row in rows:
		metrics.append(
			row.recently_sold.time_to_sale_days
			if name == "time_to_sale_days" else getattr(row.active, name)
		)
	available = [item.median for item in metrics if item.median is not None]
	median_value = median(available) if available else None
	missing_reason = (
		None if available else "aggregate_median_not_available_across_year_trim_buckets"
	)
	return MetricSummary(
		median=median_value,
		sample_count=sum(item.sample_count for item in metrics),
		missing_count=sum(item.missing_count for item in metrics),
		minimum=min((item.minimum for item in metrics if item.minimum is not None), default=None),
		maximum=max((item.maximum for item in metrics if item.maximum is not None), default=None),
		missing_reason=missing_reason,
	)


def _single(filters, name: str) -> str:
	values = _values(filters.get(name))
	if len(values) != 1:
		raise ValueError(f"Level 1 requires exactly one {name}")
	return values[0]


def _geography(filters) -> dict[str, str]:
	geography = {
		name: str(filters[name])
		for name in ("postal_code", "state")
		if name in filters
	}
	if "radius" in filters:
		geography.update({
			"distance_type": "radius",
			"distance_value": str(filters["radius"]),
			"distance_unit": "mi",
		})
	return geography


def _values(value) -> tuple[str, ...]:
	if value is None:
		return ()
	return tuple(str(item) for item in value) if isinstance(value, (list, tuple)) else (str(value),)
