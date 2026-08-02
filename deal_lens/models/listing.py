"""Source-independent listing model used by the DealLens pipeline."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from deal_lens.models.common import (
	DataWarning,
	DealerFee,
	InstalledOption,
	PriceHistoryRecord,
	SourceProvenance,
	SupplementaryStatus,
	WarrantyCoverage,
)


class ListingCondition(StrEnum):
	NEW = "New"
	USED = "Used"
	CERTIFIED = "Certified"


@dataclass(kw_only=True, slots=True)
class Seller:
	id: str | None = None
	name: str | None = None
	phone: str | None = None
	stock_number: str | None = None
	location: str | None = None
	city: str | None = None
	state: str | None = None
	postal_code: str | None = None
	latitude: float | None = None
	longitude: float | None = None
	dealer_fees: list[DealerFee] = field(default_factory=list)
	extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(kw_only=True, slots=True)
class Vehicle:
	vin: str | None = None
	year: int | None = None
	make: str | None = None
	model: str | None = None
	trim: str | None = None
	trim_version: str | None = None
	body_style: str | None = None
	drivetrain: str | None = None
	fuel_type: str | None = None
	powertrain_type: str | None = None
	transmission: str | None = None
	engine: str | None = None
	exterior_color: str | None = None
	interior_color: str | None = None
	specs: dict[str, Any] = field(default_factory=dict)


@dataclass(kw_only=True, slots=True)
class ListingDocuments:
	carfax_url: str | None = None
	autocheck_url: str | None = None
	window_sticker_url: str | None = None
	window_sticker_verified: bool | None = None


@dataclass(kw_only=True, slots=True)
class Listing:
	"""Canonical listing facts accessed through typed attributes.

	Calculated analysis results deliberately do not live on this model. ``extra``
	retains legacy fields so old saved records can cross the new boundary without
	losing information.
	"""

	id: str
	title: str | None = None
	vehicle: Vehicle = field(default_factory=Vehicle)
	condition: ListingCondition | None = None
	price: Decimal | None = None
	msrp: Decimal | None = None
	mileage: int | None = None
	days_on_market: int | None = None
	listed_at: datetime | str | None = None
	listing_url: str | None = None
	seller: Seller = field(default_factory=Seller)
	documents: ListingDocuments = field(default_factory=ListingDocuments)
	images: list[str] = field(default_factory=list)
	installed_options: list[InstalledOption] | None = None
	installed_options_total: Decimal | None = None
	price_history: list[PriceHistoryRecord] | None = None
	warranty_coverages: list[WarrantyCoverage] = field(default_factory=list)
	warranty_extra: dict[str, Any] = field(default_factory=dict, repr=False)
	source: str = "legacy"
	source_record_id: str | None = None
	provenance: dict[str, SourceProvenance] = field(default_factory=dict)
	warnings: list[DataWarning] = field(default_factory=list)
	supplementary_status: SupplementaryStatus = field(default_factory=SupplementaryStatus)
	raw_source: dict[str, Any] = field(default_factory=dict, repr=False)
	extra: dict[str, Any] = field(default_factory=dict, repr=False)

	@property
	def vin(self) -> str | None:
		return self.vehicle.vin

	@property
	def year(self) -> int | None:
		return self.vehicle.year

	def to_dict(self) -> dict[str, Any]:
		"""Serialize to the established saved-data and analysis envelope."""
		result = dict(self.extra)
		result.update({
			"id": self.id,
			"vin": self.vin,
			"title": self.title,
			"year": self.year,
			"trim": self.vehicle.trim,
			"condition": self.condition.value if self.condition else None,
			"msrp": _json_number(self.msrp),
			"price": _json_number(self.price),
			"mileage": self.mileage,
			"days_on_market": self.days_on_market,
			"listed_at": _date_value(self.listed_at),
			"listed": _date_value(self.listed_at),
			"listing_url": self.listing_url,
			"images": list(self.images),
			"seller": _seller_dict(self.seller),
			"specs": dict(self.vehicle.specs),
			"installed_addons": {
				"items": (
					[option.to_dict() for option in self.installed_options]
					if self.installed_options is not None else None
				),
				"total": _json_number(self.installed_options_total),
			},
			"price_history": (
				[item.to_dict() for item in self.price_history]
				if self.price_history is not None else None
			),
			"additional_docs": {
				"carfax_url": self.documents.carfax_url,
				"autocheck_url": self.documents.autocheck_url,
				"window_sticker_url": self.documents.window_sticker_url,
			},
			"provenance": {
				key: item.to_dict() for key, item in self.provenance.items()
			},
			"warnings": [warning.to_dict() for warning in self.warnings],
			"supplementary_status": self.supplementary_status.to_dict(),
		})
		if self.raw_source:
			result["source_data"] = self.raw_source
		result["warranty"] = (
			{
				**self.warranty_extra,
				"coverages": [coverage.to_dict() for coverage in self.warranty_coverages],
			}
			if self.warranty_coverages or self.warranty_extra else None
		)
		return result

def listing_from_legacy(value: Mapping[str, Any]) -> Listing:
	"""Adapt legacy scraper/API envelopes without inventing missing values."""
	data = dict(value)
	specs = _dict(data.get("specs"))
	seller_data = _dict(data.get("seller"))
	documents = _dict(data.get("additional_docs"))
	source_data = _dict(data.get("source_data"))
	search_source = _dict(source_data.get("search_listing"))
	detail_source = _dict(source_data.get("detail_listing"))
	vehicle_source = _dict(detail_source.get("vehicle"))
	build_source = _dict(vehicle_source.get("build"))
	metadata_vehicle = _dict(_dict(data.get("metadata")).get("vehicle"))
	warranty = _dict(data.get("warranty"))
	condition_value = data.get("condition")
	try:
		condition = ListingCondition(str(condition_value)) if condition_value else None
	except ValueError:
		condition = None
	known = {
		"id", "vin", "year", "trim", "condition", "msrp", "price",
		"mileage", "days_on_market", "listed", "listed_at", "listing_url",
		"images", "seller", "specs", "installed_addons", "price_history",
		"additional_docs", "source_data", "provenance", "warnings",
		"supplementary_status", "warranty",
	}
	addons = _dict(data.get("installed_addons"))
	typed_warnings = [
		DataWarning.from_dict(item)
		for item in data.get("warnings") or []
		if isinstance(item, Mapping)
	]
	dealer_fees = _dealer_fees(
		seller_data.get("dealer_fees"), typed_warnings
	)
	installed_options = _mapping_records(
		addons.get("items"), InstalledOption.from_dict,
		"installed_addons.items", typed_warnings,
	)
	price_history = _mapping_records(
		data.get("price_history"), PriceHistoryRecord.from_dict,
		"price_history", typed_warnings,
	)
	warranty_coverages = _mapping_records(
		warranty.get("coverages"), WarrantyCoverage.from_dict,
		"warranty.coverages", typed_warnings,
	) or []
	provenance = _provenance_records(data.get("provenance"), typed_warnings)
	return Listing(
		id=str(data.get("id") or data.get("vin") or ""),
		title=_string(data.get("title")) or _generated_title(
			data.get("year"),
			build_source.get("make") or detail_source.get("make") or search_source.get("make") or metadata_vehicle.get("make"),
			build_source.get("model") or detail_source.get("model") or search_source.get("model") or metadata_vehicle.get("model"),
			data.get("trim"),
		),
		vehicle=Vehicle(
			vin=_string(data.get("vin")),
			year=_integer(data.get("year")),
			make=_string(build_source.get("make") or detail_source.get("make") or search_source.get("make") or metadata_vehicle.get("make")),
			model=_string(build_source.get("model") or detail_source.get("model") or search_source.get("model") or metadata_vehicle.get("model")),
			trim=_string(data.get("trim")),
			trim_version=_string(specs.get("Trim Version") or data.get("trim_version")),
			body_style=_string(specs.get("Body Style") or data.get("body_style")),
			drivetrain=_string(specs.get("Drivetrain")),
			fuel_type=_string(specs.get("Fuel Type") or data.get("fuel_type")),
			powertrain_type=_string(specs.get("Powertrain Type") or data.get("powertrain_type")),
			transmission=_string(specs.get("Transmission")),
			engine=_string(specs.get("Engine")),
			exterior_color=_string(specs.get("Exterior Color")),
			interior_color=_string(specs.get("Interior Color")),
			specs=specs,
		),
		condition=condition,
		price=_decimal(data.get("price")),
		msrp=_decimal(data.get("msrp")),
		mileage=_integer(data.get("mileage")),
		days_on_market=_integer(data.get("days_on_market")),
		listed_at=data.get("listed_at") or data.get("listed"),
		listing_url=_string(data.get("listing_url")),
		seller=Seller(
			id=_string(seller_data.get("id")),
			name=_string(seller_data.get("name")),
			phone=_string(seller_data.get("phone")),
			stock_number=_string(seller_data.get("stock_number")),
			location=_string(seller_data.get("location")),
			city=_string(seller_data.get("city")),
			state=_string(seller_data.get("state")),
			postal_code=_string(seller_data.get("postal_code")),
			latitude=_float(seller_data.get("latitude")),
			longitude=_float(seller_data.get("longitude")),
			dealer_fees=dealer_fees,
			extra={
				key: item for key, item in seller_data.items()
				if key not in {
					"id", "name", "phone", "stock_number", "location", "city",
					"state", "postal_code", "latitude", "longitude", "dealer_fees",
				}
			},
		),
		documents=ListingDocuments(
			carfax_url=_string(documents.get("carfax_url")),
			autocheck_url=_string(documents.get("autocheck_url")),
			window_sticker_url=_string(documents.get("window_sticker_url")),
		),
		images=list(data.get("images") or []),
		installed_options=installed_options,
		installed_options_total=_decimal(addons.get("total")),
		price_history=price_history,
		warranty_coverages=warranty_coverages,
		warranty_extra={
			key: item for key, item in warranty.items() if key != "coverages"
		},
		source=str(source_data.get("provider") or "legacy"),
		source_record_id=str(data.get("id")) if data.get("id") is not None else None,
		provenance=provenance,
		warnings=typed_warnings,
		supplementary_status=SupplementaryStatus.from_dict(
			_dict(data.get("supplementary_status"))
		),
		raw_source=source_data,
		extra={key: item for key, item in data.items() if key not in known},
	)


def _dict(value: Any) -> dict[str, Any]:
	return dict(value) if isinstance(value, Mapping) else {}


def _boundary_warning(field: str, value: Any) -> DataWarning:
	return DataWarning(
		code="incompatible_data", field=field,
		message=f"{field} contained a child record with an incompatible type.",
		extra={"received_type": type(value).__name__},
	)


def _dealer_fees(value: Any, warnings: list[DataWarning]) -> list[DealerFee]:
	if value is None:
		return []
	if not isinstance(value, (list, tuple)):
		warnings.append(_boundary_warning("seller.dealer_fees", value))
		return []
	result = []
	for item in value:
		try:
			result.append(DealerFee.from_legacy(item))
		except ValueError:
			warnings.append(_boundary_warning("seller.dealer_fees", item))
	return result


def _mapping_records(value: Any, factory: Any, field: str, warnings: list[DataWarning]) -> list[Any] | None:
	if value is None:
		return None
	if not isinstance(value, (list, tuple)):
		warnings.append(_boundary_warning(field, value))
		return None
	result = []
	for item in value:
		if isinstance(item, Mapping):
			result.append(factory(item))
		else:
			warnings.append(_boundary_warning(field, item))
	return result


def _provenance_records(value: Any, warnings: list[DataWarning]) -> dict[str, SourceProvenance]:
	if value is None:
		return {}
	if not isinstance(value, Mapping):
		warnings.append(_boundary_warning("provenance", value))
		return {}
	result = {}
	for key, item in value.items():
		if isinstance(item, Mapping):
			result[str(key)] = SourceProvenance.from_dict(item)
		else:
			warnings.append(_boundary_warning(f"provenance.{key}", item))
	return result


def _string(value: Any) -> str | None:
	return str(value) if value is not None else None


def _generated_title(*parts: Any) -> str | None:
	result = " ".join(str(value) for value in parts if value is not None)
	return result or None


def _integer(value: Any) -> int | None:
	if value is None or isinstance(value, bool):
		return None
	try:
		return int(value)
	except (TypeError, ValueError):
		return None


def _float(value: Any) -> float | None:
	if value is None or isinstance(value, bool):
		return None
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


def _decimal(value: Any) -> Decimal | None:
	if value is None or isinstance(value, bool):
		return None
	try:
		return Decimal(str(value))
	except (InvalidOperation, ValueError):
		return None


def _json_number(value: Decimal | None) -> int | float | None:
	if value is None:
		return None
	return int(value) if value == value.to_integral_value() else float(value)


def _date_value(value: datetime | str | None) -> str | None:
	return value.isoformat() if isinstance(value, datetime) else value


def _seller_dict(value: Seller) -> dict[str, Any]:
	result: dict[str, Any] = {
		**value.extra,
		"name": value.name,
		"location": value.location,
		"phone": value.phone,
		"stock_number": value.stock_number,
	}
	for key, item in (
		("id", value.id), ("city", value.city), ("state", value.state),
		("postal_code", value.postal_code), ("latitude", value.latitude),
		("longitude", value.longitude),
	):
		if item is not None:
			result[key] = item
	if value.dealer_fees:
		result["dealer_fees"] = [fee.to_legacy() for fee in value.dealer_fees]
	return result
