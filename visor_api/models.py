"""Typed models for Visor Public API inventory responses."""

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Any, Self


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
	if not isinstance(value, Mapping):
		raise ValueError(f"{path} must be an object")
	return value


def _list(value: Any, path: str) -> list[Any]:
	if not isinstance(value, list):
		raise ValueError(f"{path} must be an array")
	return value


def _required(data: Mapping[str, Any], name: str, path: str) -> Any:
	if name not in data:
		raise ValueError(f"{path}.{name} is required")
	return data[name]


def _extras(data: Mapping[str, Any], names: set[str]) -> dict[str, Any]:
	return {name: value for name, value in data.items() if name not in names}


@dataclass(frozen=True, kw_only=True)
class APIModel:
	"""Base behavior shared by normalized API models."""

	extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

	def to_dict(self) -> dict[str, Any]:
		result = {
			model_field.name: _serialize(getattr(self, model_field.name))
			for model_field in fields(self)
		}
		extras = result.pop("extra_fields")
		result.update(extras)
		return result


def _serialize(value: Any) -> Any:
	if isinstance(value, APIModel):
		return value.to_dict()
	if isinstance(value, list):
		return [_serialize(item) for item in value]
	if isinstance(value, dict):
		return {name: _serialize(item) for name, item in value.items()}
	return value


@dataclass(frozen=True, kw_only=True)
class Option(APIModel):
	code: str
	name: str
	msrp: int | float | None

	@classmethod
	def from_dict(cls, value: Any, path: str = "option") -> Self:
		data = _mapping(value, path)
		return cls(
			code=str(_required(data, "code", path)),
			name=str(_required(data, "name", path)),
			msrp=_required(data, "msrp", path),
			extra_fields=_extras(data, {"code", "name", "msrp"}),
		)


@dataclass(frozen=True, kw_only=True)
class PriceHistoryEntry(APIModel):
	changed_at: str
	miles: int | float | None
	price_before: int | float | None
	price_after: int | float | None

	@classmethod
	def from_dict(cls, value: Any, path: str = "price_history[]") -> Self:
		data = _mapping(value, path)
		return cls(
			changed_at=str(_required(data, "changed_at", path)),
			miles=_required(data, "miles", path),
			price_before=_required(data, "price_before", path),
			price_after=_required(data, "price_after", path),
			extra_fields=_extras(data, {"changed_at", "miles", "price_before", "price_after"}),
		)


@dataclass(frozen=True, kw_only=True)
class Pagination(APIModel):
	limit: int
	offset: int
	total: int
	next_offset: int | None

	@classmethod
	def from_dict(cls, value: Any, path: str = "pagination") -> Self:
		data = _mapping(value, path)
		next_offset = _required(data, "next_offset", path)
		return cls(
			limit=int(_required(data, "limit", path)),
			offset=int(_required(data, "offset", path)),
			total=int(_required(data, "total", path)),
			next_offset=int(next_offset) if next_offset is not None else None,
			extra_fields=_extras(data, {"limit", "offset", "total", "next_offset"}),
		)


LISTING_SUMMARY_FIELDS = {
	"year", "make", "model", "trim", "version", "body_type", "drivetrain",
	"fuel_type", "powertrain_type", "transmission", "engine", "cylinders",
	"doors", "seating_capacity", "exterior_color", "interior_color",
	"base_exterior_color", "base_interior_color", "msrp", "discount_from_msrp",
	"price", "miles", "days_on_market", "listed_at", "status",
	"inventory_status", "availability_status", "inventory_type", "stock_number",
	"vdp_url", "sold_date", "dealer_id", "dealer_name", "dealer_type", "city",
	"state", "postal_code", "latitude", "longitude", "distance_miles",
}


@dataclass(frozen=True, kw_only=True)
class ListingSummary(APIModel):
	id: str
	vin: str
	year: int | None = None
	make: str | None = None
	model: str | None = None
	trim: str | None = None
	version: str | None = None
	body_type: str | None = None
	drivetrain: str | None = None
	fuel_type: str | None = None
	powertrain_type: str | None = None
	transmission: str | None = None
	engine: str | None = None
	cylinders: int | float | None = None
	doors: int | float | None = None
	seating_capacity: int | float | None = None
	exterior_color: str | None = None
	interior_color: str | None = None
	base_exterior_color: str | None = None
	base_interior_color: str | None = None
	msrp: int | float | None = None
	discount_from_msrp: int | float | None = None
	price: int | float | None = None
	miles: int | float | None = None
	days_on_market: int | float | None = None
	listed_at: str | None = None
	status: str | None = None
	inventory_status: str | None = None
	availability_status: str | None = None
	inventory_type: str | None = None
	stock_number: str | None = None
	vdp_url: str | None = None
	sold_date: str | None = None
	dealer_id: str | None = None
	dealer_name: str | None = None
	dealer_type: str | None = None
	city: str | None = None
	state: str | None = None
	postal_code: str | None = None
	latitude: int | float | None = None
	longitude: int | float | None = None
	distance_miles: int | float | None = None
	photo_urls: list[str] | None = None
	features: list[str] | None = None
	options_packages: list[str] | None = None
	price_history: list[PriceHistoryEntry] | None = None
	options: list[Option] | None = None

	@classmethod
	def from_dict(cls, value: Any, path: str = "data[]") -> Self:
		data = _mapping(value, path)
		collections = {}
		for name in ("photo_urls", "features", "options_packages"):
			collections[name] = (
				[str(item) for item in _list(data[name], f"{path}.{name}")]
				if data.get(name) is not None else None
			)
		history = data.get("price_history")
		options = data.get("options")
		known = {"id", "vin", *LISTING_SUMMARY_FIELDS, *collections, "price_history", "options"}
		return cls(
			id=str(_required(data, "id", path)),
			vin=str(_required(data, "vin", path)),
			**{name: data.get(name) for name in LISTING_SUMMARY_FIELDS},
			**collections,
			price_history=(
				[PriceHistoryEntry.from_dict(item, f"{path}.price_history[]") for item in _list(history, f"{path}.price_history")]
				if history is not None else None
			),
			options=(
				[Option.from_dict(item, f"{path}.options[]") for item in _list(options, f"{path}.options")]
				if options is not None else None
			),
			extra_fields=_extras(data, known),
		)


@dataclass(frozen=True, kw_only=True)
class ListingSearchResponse(APIModel):
	data: list[ListingSummary]
	pagination: Pagination
	meta: dict[str, Any]

	@classmethod
	def from_dict(cls, value: Any) -> Self:
		data = _mapping(value, "response")
		return cls(
			data=[ListingSummary.from_dict(item) for item in _list(_required(data, "data", "response"), "response.data")],
			pagination=Pagination.from_dict(_required(data, "pagination", "response")),
			meta=dict(_mapping(_required(data, "meta", "response"), "response.meta")),
			extra_fields=_extras(data, {"data", "pagination", "meta"}),
		)


@dataclass(frozen=True, kw_only=True)
class PricingLineItem(APIModel):
	amount_usd: int | float | None
	applicability: str | None
	direction: str | None
	raw_label: str | None
	role: str | None
	row_key: str | None
	row_order: int | float | None
	subtype: str | None

	@classmethod
	def from_dict(cls, value: Any) -> Self:
		data = _mapping(value, "pricing.line_items[]")
		names = {"amount_usd", "applicability", "direction", "raw_label", "role", "row_key", "row_order", "subtype"}
		return cls(**{name: _required(data, name, "pricing.line_items[]") for name in names}, extra_fields=_extras(data, names))


@dataclass(frozen=True, kw_only=True)
class Pricing(APIModel):
	displayed_provider_current_price_usd: int | float | None
	seller_total_before_taxes_and_registration_usd: int | float | None
	seller_total_before_manufacturer_incentives_usd: int | float | None
	dealer_adjusted_vehicle_price_usd: int | float | None
	line_items: list[PricingLineItem]

	@classmethod
	def from_dict(cls, value: Any) -> Self:
		data = _mapping(value, "pricing")
		names = {
			"displayed_provider_current_price_usd",
			"seller_total_before_taxes_and_registration_usd",
			"seller_total_before_manufacturer_incentives_usd",
			"dealer_adjusted_vehicle_price_usd",
		}
		return cls(
			**{name: _required(data, name, "pricing") for name in names},
			line_items=[PricingLineItem.from_dict(item) for item in _list(_required(data, "line_items", "pricing"), "pricing.line_items")],
			extra_fields=_extras(data, names | {"line_items"}),
		)


@dataclass(frozen=True, kw_only=True)
class Dealer(APIModel):
	dealer_id: str | None
	name: str | None
	city: str | None
	state: str | None
	postal_code: str | None
	latitude: int | float | None
	longitude: int | float | None
	phone: str | None

	@classmethod
	def from_dict(cls, value: Any) -> Self:
		data = _mapping(value, "dealer")
		names = {"dealer_id", "name", "city", "state", "postal_code", "latitude", "longitude", "phone"}
		return cls(**{name: _required(data, name, "dealer") for name in names}, extra_fields=_extras(data, names))


BUILD_FIELDS = {
	"year", "make", "model", "trim", "version", "body_type", "drivetrain",
	"fuel_type", "powertrain_type", "transmission", "engine", "cylinders",
	"doors", "seating_capacity", "exterior_color", "interior_color",
	"base_exterior_color", "base_interior_color", "assembly_location",
	"window_sticker_verified", "base_msrp", "combined_msrp",
}


@dataclass(frozen=True, kw_only=True)
class VehicleBuild(APIModel):
	year: int | float | None
	make: str | None
	model: str | None
	trim: str | None
	version: str | None
	body_type: str | None
	drivetrain: str | None
	fuel_type: str | None
	powertrain_type: str | None
	transmission: str | None
	engine: str | None
	cylinders: int | float | None
	doors: int | float | None
	seating_capacity: int | float | None
	exterior_color: str | None
	interior_color: str | None
	base_exterior_color: str | None
	base_interior_color: str | None
	assembly_location: str | None
	window_sticker_verified: bool
	base_msrp: int | float | None
	combined_msrp: int | float | None
	options: list[Option] | None

	@classmethod
	def from_dict(cls, value: Any) -> Self:
		data = _mapping(value, "vehicle.build")
		options = _required(data, "options", "vehicle.build")
		return cls(
			**{name: _required(data, name, "vehicle.build") for name in BUILD_FIELDS},
			options=(
				[Option.from_dict(item, "vehicle.build.options[]") for item in _list(options, "vehicle.build.options")]
				if options is not None else None
			),
			extra_fields=_extras(data, BUILD_FIELDS | {"options"}),
		)


@dataclass(frozen=True, kw_only=True)
class Vehicle(APIModel):
	vin: str
	status: str
	build: VehicleBuild

	@classmethod
	def from_dict(cls, value: Any) -> Self:
		data = _mapping(value, "vehicle")
		return cls(
			vin=str(_required(data, "vin", "vehicle")),
			status=str(_required(data, "status", "vehicle")),
			build=VehicleBuild.from_dict(_required(data, "build", "vehicle")),
			extra_fields=_extras(data, {"vin", "status", "build"}),
		)


@dataclass(frozen=True, kw_only=True)
class ListingDetail(APIModel):
	id: str | None
	vin: str
	status: str
	price: int | float | None
	miles: int | float | None
	inventory_type: str | None
	stock_number: str | None
	vdp_url: str | None
	vhr_url: str | None
	photo_urls: list[str]
	photo_url_primary: str | None
	pricing: Pricing | None
	inventory_date: str | None
	sold_date: str | None
	last_checked_at: str | None
	dealer: Dealer
	vehicle: Vehicle
	price_history: list[PriceHistoryEntry] | None

	@classmethod
	def from_dict(cls, value: Any) -> Self:
		data = _mapping(value, "data")
		scalars = {"id", "vin", "status", "price", "miles", "inventory_type", "stock_number", "vdp_url", "vhr_url", "photo_url_primary", "inventory_date", "sold_date", "last_checked_at"}
		pricing = _required(data, "pricing", "data")
		history = _required(data, "price_history", "data")
		return cls(
			**{name: _required(data, name, "data") for name in scalars},
			photo_urls=[str(item) for item in _list(_required(data, "photo_urls", "data"), "data.photo_urls")],
			pricing=Pricing.from_dict(pricing) if pricing is not None else None,
			dealer=Dealer.from_dict(_required(data, "dealer", "data")),
			vehicle=Vehicle.from_dict(_required(data, "vehicle", "data")),
			price_history=(
				[PriceHistoryEntry.from_dict(item) for item in _list(history, "data.price_history")]
				if history is not None else None
			),
			extra_fields=_extras(data, scalars | {"photo_urls", "pricing", "dealer", "vehicle", "price_history"}),
		)


@dataclass(frozen=True, kw_only=True)
class ListingDetailResponse(APIModel):
	data: ListingDetail
	meta: dict[str, Any]

	@classmethod
	def from_dict(cls, value: Any) -> Self:
		data = _mapping(value, "response")
		return cls(
			data=ListingDetail.from_dict(_required(data, "data", "response")),
			meta=dict(_mapping(_required(data, "meta", "response"), "response.meta")),
			extra_fields=_extras(data, {"data", "meta"}),
		)


@dataclass(frozen=True, kw_only=True)
class FacetMetric(APIModel):
	name: str
	value: int | float | None
	null_reason: str | None = None

	@classmethod
	def from_dict(cls, value: Any) -> Self:
		data = _mapping(value, "facet.metric")
		return cls(name=str(_required(data, "name", "facet.metric")), value=_required(data, "value", "facet.metric"), null_reason=data.get("null_reason"), extra_fields=_extras(data, {"name", "value", "null_reason"}))


@dataclass(frozen=True, kw_only=True)
class FacetValue(APIModel):
	value: str
	count: int
	metric: FacetMetric | None = None
	name: str | None = None
	msrp: int | float | None = None
	avg_price: int | float | None = None
	avg_days_on_lot: int | float | None = None
	avg_discount: int | float | None = None

	@classmethod
	def from_dict(cls, value: Any) -> Self:
		data = _mapping(value, "facets[]")
		metric = data.get("metric")
		names = {"value", "count", "metric", "name", "msrp", "avg_price", "avg_days_on_lot", "avg_discount"}
		return cls(value=str(_required(data, "value", "facets[]")), count=int(_required(data, "count", "facets[]")), metric=FacetMetric.from_dict(metric) if metric is not None else None, name=data.get("name"), msrp=data.get("msrp"), avg_price=data.get("avg_price"), avg_days_on_lot=data.get("avg_days_on_lot"), avg_discount=data.get("avg_discount"), extra_fields=_extras(data, names))


@dataclass(frozen=True, kw_only=True)
class RangeBucket(APIModel):
	min: int | float
	max: int | float
	count: int

	@classmethod
	def from_dict(cls, value: Any) -> Self:
		data = _mapping(value, "range.buckets[]")
		return cls(min=_required(data, "min", "range.buckets[]"), max=_required(data, "max", "range.buckets[]"), count=int(_required(data, "count", "range.buckets[]")), extra_fields=_extras(data, {"min", "max", "count"}))


@dataclass(frozen=True, kw_only=True)
class RangeFacet(APIModel):
	buckets: list[RangeBucket]
	interval: int | float | None
	min: int | float
	max: int | float

	@classmethod
	def from_dict(cls, value: Any) -> Self:
		data = _mapping(value, "range_facets")
		return cls(buckets=[RangeBucket.from_dict(item) for item in _list(_required(data, "buckets", "range_facets"), "range_facets.buckets")], interval=_required(data, "interval", "range_facets"), min=_required(data, "min", "range_facets"), max=_required(data, "max", "range_facets"), extra_fields=_extras(data, {"buckets", "interval", "min", "max"}))


@dataclass(frozen=True, kw_only=True)
class FacetStats(APIModel):
	min: int | float
	max: int | float
	count: int
	missing: int
	mean: int | float
	median: int | float
	stddev: int | float

	@classmethod
	def from_dict(cls, value: Any) -> Self:
		data = _mapping(value, "stats")
		names = {"min", "max", "count", "missing", "mean", "median", "stddev"}
		return cls(**{name: _required(data, name, "stats") for name in names}, extra_fields=_extras(data, names))


@dataclass(frozen=True, kw_only=True)
class FacetData(APIModel):
	total: int
	facets: dict[str, list[FacetValue]]
	range_facets: dict[str, RangeFacet]
	stats: dict[str, FacetStats]

	@classmethod
	def from_dict(cls, value: Any) -> Self:
		data = _mapping(value, "data")
		facets = _mapping(_required(data, "facets", "data"), "data.facets")
		ranges = _mapping(_required(data, "range_facets", "data"), "data.range_facets")
		stats = _mapping(_required(data, "stats", "data"), "data.stats")
		return cls(total=int(_required(data, "total", "data")), facets={name: [FacetValue.from_dict(item) for item in _list(values, f"data.facets.{name}")] for name, values in facets.items()}, range_facets={name: RangeFacet.from_dict(item) for name, item in ranges.items()}, stats={name: FacetStats.from_dict(item) for name, item in stats.items()}, extra_fields=_extras(data, {"total", "facets", "range_facets", "stats"}))


@dataclass(frozen=True, kw_only=True)
class FacetMeta(APIModel):
	facets: list[str]
	metric: str
	sort: str
	minimum_metric_count: int

	@classmethod
	def from_dict(cls, value: Any) -> Self:
		data = _mapping(value, "meta")
		return cls(facets=[str(item) for item in _list(_required(data, "facets", "meta"), "meta.facets")], metric=str(_required(data, "metric", "meta")), sort=str(_required(data, "sort", "meta")), minimum_metric_count=int(_required(data, "minimum_metric_count", "meta")), extra_fields=_extras(data, {"facets", "metric", "sort", "minimum_metric_count"}))


@dataclass(frozen=True, kw_only=True)
class FacetResponse(APIModel):
	data: FacetData
	meta: FacetMeta

	@classmethod
	def from_dict(cls, value: Any) -> Self:
		data = _mapping(value, "response")
		return cls(data=FacetData.from_dict(_required(data, "data", "response")), meta=FacetMeta.from_dict(_required(data, "meta", "response")), extra_fields=_extras(data, {"data", "meta"}))
