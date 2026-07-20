"""Stable, facet-native data contract for Level 1 market analysis.

These models intentionally describe aggregate market data only.  They do not
inherit from, contain, or serialize through the legacy listing compatibility
schema used by Levels 2 and 3.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Self


Number = int | float


class MarketCohort(StrEnum):
	"""The inventory population measured by a Level 1 facet query."""

	ACTIVE = "active"
	RECENTLY_SOLD = "recently_sold"


class ConfidenceLevel(StrEnum):
	HIGH = "high"
	MODERATE = "moderate"
	LOW = "low"


@dataclass(frozen=True, kw_only=True)
class MarketConfidence:
	level: ConfidenceLevel
	minimum_metric_count: int
	trim_bucket_count: int
	price_supported_bucket_count: int
	active_days_supported_bucket_count: int
	recent_sales_supported_bucket_count: int
	price_missing_rate: float | None
	mileage_missing_rate: float | None
	active_days_missing_rate: float | None
	recent_sales_missing_rate: float | None
	maximum_price_coefficient_of_variation: float | None
	maximum_mileage_coefficient_of_variation: float | None
	kbb_mapping_rate: float | None
	limitations: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class MetricSummary:
	"""A metric value together with its completeness and dispersion."""

	median: Number | None
	sample_count: int
	missing_count: int
	minimum: Number | None = None
	maximum: Number | None = None
	standard_deviation: Number | None = None
	missing_reason: str | None = None

	def __post_init__(self) -> None:
		_validate_count(self.sample_count, "sample_count")
		_validate_count(self.missing_count, "missing_count")
		if self.median is None and not self.missing_reason:
			raise ValueError("missing_reason is required when median is unavailable")
		if self.median is not None and self.sample_count == 0:
			raise ValueError("median requires at least one sample")
		if (
			self.minimum is not None
			and self.maximum is not None
			and self.minimum > self.maximum
		):
			raise ValueError("minimum cannot exceed maximum")


@dataclass(frozen=True, kw_only=True)
class ActiveInventoryMetrics:
	"""Current asking-market metrics; days measure active listing age."""

	inventory_count: int
	asking_price: MetricSummary
	mileage: MetricSummary
	listing_age_days: MetricSummary

	def __post_init__(self) -> None:
		_validate_count(self.inventory_count, "inventory_count")


@dataclass(frozen=True, kw_only=True)
class RecentlySoldMetrics:
	"""Historical sales metrics; days measure observed time to sale."""

	inventory_count: int
	time_to_sale_days: MetricSummary

	def __post_init__(self) -> None:
		_validate_count(self.inventory_count, "inventory_count")


@dataclass(frozen=True, kw_only=True)
class MarketQueryProvenance:
	"""The complete logical query associated with one source response."""

	cohort: MarketCohort
	metric: str
	endpoint: str
	filters: dict[str, str | tuple[str, ...]]
	minimum_metric_count: int
	retrieved_at: str

	def __post_init__(self) -> None:
		if not self.metric.strip():
			raise ValueError("metric cannot be empty")
		if not self.endpoint.strip():
			raise ValueError("endpoint cannot be empty")
		_validate_count(self.minimum_metric_count, "minimum_metric_count")
		_validate_timestamp(self.retrieved_at)


@dataclass(frozen=True, kw_only=True)
class MarketSearchScope:
	"""User-visible boundaries shared by all Level 1 summaries.

	``selected_trims`` restricts the market; an empty tuple includes all trims.
	"""

	make: str
	model: str
	years: tuple[int, ...]
	conditions: tuple[str, ...]
	geography: dict[str, str]
	selected_trims: tuple[str, ...] = ()
	additional_filters: dict[str, str | tuple[str, ...]] = field(default_factory=dict)

	def __post_init__(self) -> None:
		if not self.make.strip() or not self.model.strip():
			raise ValueError("make and model cannot be empty")
		if not self.years:
			raise ValueError("at least one model year is required")
		if len(set(self.years)) != len(self.years):
			raise ValueError("years must be unique")
		if any(not trim.strip() for trim in self.selected_trims):
			raise ValueError("selected trims cannot be empty")
		if len({trim.casefold() for trim in self.selected_trims}) != len(self.selected_trims):
			raise ValueError("selected trims must be unique")


@dataclass(frozen=True, kw_only=True)
class YearTrimSummary:
	"""Aggregate comparison data for one canonical Visor year/trim bucket."""

	year: int
	trim: str
	active: ActiveInventoryMetrics
	recently_sold: RecentlySoldMetrics

	def __post_init__(self) -> None:
		if not self.trim.strip():
			raise ValueError("trim cannot be empty")


@dataclass(frozen=True, kw_only=True)
class MarketSnapshot:
	"""Complete Level 1 aggregate result consumed by analysis and reporting."""

	scope: MarketSearchScope
	active: ActiveInventoryMetrics
	recently_sold: RecentlySoldMetrics
	year_trim_summaries: tuple[YearTrimSummary, ...]
	queries: tuple[MarketQueryProvenance, ...]
	confidence: MarketConfidence
	generated_at: str

	def __post_init__(self) -> None:
		_validate_timestamp(self.generated_at)
		keys = [(summary.year, summary.trim.casefold()) for summary in self.year_trim_summaries]
		if len(set(keys)) != len(keys):
			raise ValueError("year/trim summaries must be unique")
		unknown_years = {year for year, _ in keys} - set(self.scope.years)
		if unknown_years:
			raise ValueError("year/trim summaries must fall within the search scope")

	def to_dict(self) -> dict[str, Any]:
		"""Return a JSON-compatible representation of this contract."""
		return _serialize(asdict(self))

	@classmethod
	def from_dict(cls, value: dict[str, Any]) -> Self:
		"""Restore a snapshot from cached or persisted contract data."""
		return cls(
			scope=_scope_from_dict(value["scope"]),
			active=_active_from_dict(value["active"]),
			recently_sold=_sold_from_dict(value["recently_sold"]),
			year_trim_summaries=tuple(
				YearTrimSummary(
					year=item["year"],
					trim=item["trim"],
					active=_active_from_dict(item["active"]),
					recently_sold=_sold_from_dict(item["recently_sold"]),
				)
				for item in value["year_trim_summaries"]
			),
			queries=tuple(
				MarketQueryProvenance(
					cohort=MarketCohort(item["cohort"]),
					metric=item["metric"],
					endpoint=item["endpoint"],
					filters=_query_filters(item["filters"]),
					minimum_metric_count=item["minimum_metric_count"],
					retrieved_at=item["retrieved_at"],
				)
				for item in value["queries"]
			),
			confidence=_confidence_from_dict(value["confidence"]),
			generated_at=value["generated_at"],
		)


def _metric_from_dict(value: dict[str, Any]) -> MetricSummary:
	return MetricSummary(**value)


def _confidence_from_dict(value: dict[str, Any]) -> MarketConfidence:
	return MarketConfidence(
		level=ConfidenceLevel(value["level"]),
		minimum_metric_count=value["minimum_metric_count"],
		trim_bucket_count=value["trim_bucket_count"],
		price_supported_bucket_count=value["price_supported_bucket_count"],
		active_days_supported_bucket_count=value["active_days_supported_bucket_count"],
		recent_sales_supported_bucket_count=value["recent_sales_supported_bucket_count"],
		price_missing_rate=value["price_missing_rate"],
		mileage_missing_rate=value["mileage_missing_rate"],
		active_days_missing_rate=value["active_days_missing_rate"],
		recent_sales_missing_rate=value["recent_sales_missing_rate"],
		maximum_price_coefficient_of_variation=value[
			"maximum_price_coefficient_of_variation"
		],
		maximum_mileage_coefficient_of_variation=value[
			"maximum_mileage_coefficient_of_variation"
		],
		kbb_mapping_rate=value["kbb_mapping_rate"],
		limitations=tuple(value.get("limitations", ())),
	)


def _active_from_dict(value: dict[str, Any]) -> ActiveInventoryMetrics:
	return ActiveInventoryMetrics(
		inventory_count=value["inventory_count"],
		asking_price=_metric_from_dict(value["asking_price"]),
		mileage=_metric_from_dict(value["mileage"]),
		listing_age_days=_metric_from_dict(value["listing_age_days"]),
	)


def _sold_from_dict(value: dict[str, Any]) -> RecentlySoldMetrics:
	return RecentlySoldMetrics(
		inventory_count=value["inventory_count"],
		time_to_sale_days=_metric_from_dict(value["time_to_sale_days"]),
	)


def _scope_from_dict(value: dict[str, Any]) -> MarketSearchScope:
	return MarketSearchScope(
		make=value["make"],
		model=value["model"],
		years=tuple(value["years"]),
		conditions=tuple(value["conditions"]),
		geography=dict(value["geography"]),
		selected_trims=tuple(value.get("selected_trims", ())),
		additional_filters=_query_filters(value.get("additional_filters", {})),
	)


def _query_filters(value: dict[str, Any]) -> dict[str, str | tuple[str, ...]]:
	return {
		name: tuple(item) if isinstance(item, list) else item
		for name, item in value.items()
	}


def _serialize(value: Any) -> Any:
	if isinstance(value, StrEnum):
		return value.value
	if isinstance(value, tuple):
		return [_serialize(item) for item in value]
	if isinstance(value, list):
		return [_serialize(item) for item in value]
	if isinstance(value, dict):
		return {name: _serialize(item) for name, item in value.items()}
	return value


def _validate_count(value: int, name: str) -> None:
	if isinstance(value, bool) or not isinstance(value, int) or value < 0:
		raise ValueError(f"{name} must be a non-negative integer")


def _validate_timestamp(value: str) -> None:
	try:
		parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
	except (AttributeError, ValueError) as error:
		raise ValueError("timestamps must use ISO 8601 format") from error
	if parsed.tzinfo is None or parsed.utcoffset() is None:
		raise ValueError("timestamps must include a UTC offset")
