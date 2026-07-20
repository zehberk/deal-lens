"""Execute and align facet responses for Level 1 market analysis."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from analysis.level1_models import MarketCohort
from visor_api.client import QueryParams
from visor_api.level1_query import (
	LEVEL1_FACET_SORT,
	Level1FacetQuery,
	build_level1_facet_query_plan,
)
from visor_api.models import FacetResponse, FacetValue
from visor_api.query import VisorListingQuery


TRIM_BUCKET_NOT_RETURNED = "trim_bucket_not_returned"
METRIC_NOT_RETURNED = "metric_not_returned"
METRIC_VALUE_UNAVAILABLE = "metric_value_unavailable"


class FacetClient(Protocol):
	"""Client capability required by the Level 1 facet service."""

	def filter_facets_model(self, params: QueryParams | None = None) -> FacetResponse: ...


class Level1FacetResponseError(ValueError):
	"""Raised when a facet response does not match its planned request."""


@dataclass(frozen=True, kw_only=True)
class RetrievedLevel1Facet:
	query: Level1FacetQuery
	response: FacetResponse
	retrieved_at: str
	usage_headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class Level1TrimFacetBucket:
	year: int
	trim: str
	active_inventory_count: int | None
	recently_sold_inventory_count: int | None
	active_price_median: int | float | None
	active_days_on_market_median: int | float | None
	recently_sold_days_on_market_median: int | float | None
	active_price_missing_reason: str | None = None
	active_days_on_market_missing_reason: str | None = None
	recently_sold_days_on_market_missing_reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class Level1YearFacetResult:
	year: int
	active_inventory_count: int
	recently_sold_inventory_count: int
	trims: tuple[Level1TrimFacetBucket, ...]


@dataclass(frozen=True, kw_only=True)
class Level1FacetCollection:
	years: tuple[Level1YearFacetResult, ...]
	responses: tuple[RetrievedLevel1Facet, ...]


def collect_level1_facets(
	client: FacetClient,
	query: VisorListingQuery,
	*,
	clock: Callable[[], datetime] | None = None,
) -> Level1FacetCollection:
	"""Execute the Level 1 plan and align canonical trim buckets by year."""
	now = clock or (lambda: datetime.now(timezone.utc))
	retrieved: list[RetrievedLevel1Facet] = []
	for planned_query in build_level1_facet_query_plan(query):
		response = client.filter_facets_model(planned_query.api_params())
		retrieved.append(RetrievedLevel1Facet(
			query=planned_query,
			response=response,
			retrieved_at=_aware_isoformat(now()),
		))

	return assemble_level1_facets(tuple(retrieved))


def assemble_level1_facets(
	responses: tuple[RetrievedLevel1Facet, ...],
) -> Level1FacetCollection:
	"""Validate and align already-retrieved Level 1 facet responses."""
	for item in responses:
		_validate_response(item.query, item.response)
	years = sorted({item.query.year for item in responses})
	return Level1FacetCollection(
		years=tuple(_merge_year(year, list(responses)) for year in years),
		responses=responses,
	)


def _merge_year(
	year: int,
	responses: list[RetrievedLevel1Facet],
) -> Level1YearFacetResult:
	by_key = {
		(item.query.cohort, item.query.metric): item.response
		for item in responses
		if item.query.year == year
	}
	price_response = by_key[(MarketCohort.ACTIVE, "price.median")]
	active_days_response = by_key[(MarketCohort.ACTIVE, "days_on_market.median")]
	sold_days_response = by_key[(MarketCohort.RECENTLY_SOLD, "days_on_market.median")]

	price = _trim_map(price_response)
	active_days = _trim_map(active_days_response)
	sold_days = _trim_map(sold_days_response)
	trim_names = sorted({*price, *active_days, *sold_days}, key=str.casefold)
	trims = tuple(
		_merge_trim(year, trim, price.get(trim), active_days.get(trim), sold_days.get(trim))
		for trim in trim_names
	)
	return Level1YearFacetResult(
		year=year,
		active_inventory_count=price_response.data.total,
		recently_sold_inventory_count=sold_days_response.data.total,
		trims=trims,
	)


def _merge_trim(
	year: int,
	trim: str,
	price: FacetValue | None,
	active_days: FacetValue | None,
	sold_days: FacetValue | None,
) -> Level1TrimFacetBucket:
	active_count = price.count if price is not None else (
		active_days.count if active_days is not None else None
	)
	price_value, price_reason = _metric_value(price)
	active_days_value, active_days_reason = _metric_value(active_days)
	sold_days_value, sold_days_reason = _metric_value(sold_days)
	return Level1TrimFacetBucket(
		year=year,
		trim=trim,
		active_inventory_count=active_count,
		recently_sold_inventory_count=(sold_days.count if sold_days is not None else None),
		active_price_median=price_value,
		active_days_on_market_median=active_days_value,
		recently_sold_days_on_market_median=sold_days_value,
		active_price_missing_reason=price_reason,
		active_days_on_market_missing_reason=active_days_reason,
		recently_sold_days_on_market_missing_reason=sold_days_reason,
	)


def _trim_map(response: FacetResponse) -> dict[str, FacetValue]:
	return {bucket.value: bucket for bucket in response.data.facets.get("trim", [])}


def _metric_value(bucket: FacetValue | None) -> tuple[int | float | None, str | None]:
	if bucket is None:
		return None, TRIM_BUCKET_NOT_RETURNED
	if bucket.metric is None:
		return None, METRIC_NOT_RETURNED
	if bucket.metric.value is None:
		return None, bucket.metric.null_reason or METRIC_VALUE_UNAVAILABLE
	return bucket.metric.value, None


def _validate_response(query: Level1FacetQuery, response: FacetResponse) -> None:
	if response.meta.metric != query.metric:
		raise Level1FacetResponseError(
			f"expected metric {query.metric!r}, received {response.meta.metric!r}"
		)
	if response.meta.sort != LEVEL1_FACET_SORT:
		raise Level1FacetResponseError(
			f"expected facet sort {LEVEL1_FACET_SORT!r}, received {response.meta.sort!r}"
		)
	if "trim" not in response.meta.facets or "trim" not in response.data.facets:
		raise Level1FacetResponseError("facet response is missing trim buckets")


def _aware_isoformat(value: datetime) -> str:
	if value.tzinfo is None or value.utcoffset() is None:
		raise ValueError("Level 1 retrieval clock must return an aware datetime")
	return value.isoformat()
