"""Source-independent listing model used by the DealLens pipeline."""

from collections.abc import Iterator, Mapping, MutableMapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from deal_lens.models.common import SupplementaryStatus


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
	dealer_fees: list[dict[str, Any]] = field(default_factory=list)


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


@dataclass(kw_only=True, slots=True, eq=False)
class Listing(MutableMapping[str, Any]):
	"""Canonical listing facts with a compatibility mapping during migration.

	Calculated analysis results deliberately do not live on this model. ``extra``
	retains legacy fields so old saved records can cross the new boundary without
	losing information.
	"""

	id: str
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
	installed_options: list[dict[str, Any]] | None = None
	installed_options_total: Decimal | None = None
	price_history: list[dict[str, Any]] | None = None
	warranty_coverages: list[dict[str, Any]] = field(default_factory=list)
	source: str = "legacy"
	source_record_id: str | None = None
	provenance: dict[str, Any] = field(default_factory=dict)
	warnings: list[dict[str, Any]] = field(default_factory=list)
	supplementary_status: SupplementaryStatus = field(default_factory=SupplementaryStatus)
	raw_source: dict[str, Any] = field(default_factory=dict, repr=False)
	extra: dict[str, Any] = field(default_factory=dict, repr=False)

	@property
	def vin(self) -> str | None:
		return self.vehicle.vin

	@property
	def year(self) -> int | None:
		return self.vehicle.year

	@property
	def title(self) -> str | None:
		existing = self.extra.get("title")
		if existing:
			return str(existing)
		parts = (self.year, self.vehicle.make, self.vehicle.model, self.vehicle.trim)
		result = " ".join(str(value) for value in parts if value is not None)
		return result or None

	def to_legacy_dict(self) -> dict[str, Any]:
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
				"items": self.installed_options,
				"total": _json_number(self.installed_options_total),
			},
			"price_history": self.price_history,
			"additional_docs": {
				"carfax_url": self.documents.carfax_url,
				"autocheck_url": self.documents.autocheck_url,
				"window_sticker_url": self.documents.window_sticker_url,
			},
			"provenance": self.provenance,
			"warnings": self.warnings,
			"supplementary_status": self.supplementary_status.to_dict(),
		})
		if self.raw_source:
			result["source_data"] = self.raw_source
		if "warranty" not in result:
			result["warranty"] = (
				{"coverages": self.warranty_coverages}
				if self.warranty_coverages else None
			)
		return result

	def __getitem__(self, key: str) -> Any:
		return self.to_legacy_dict()[key]

	def __iter__(self) -> Iterator[str]:
		return iter(self.to_legacy_dict())

	def __len__(self) -> int:
		return len(self.to_legacy_dict())

	def __setitem__(self, key: str, value: Any) -> None:
		if key == "price":
			self.price = _decimal(value)
		elif key == "msrp":
			self.msrp = _decimal(value)
		elif key == "mileage":
			self.mileage = _integer(value)
		elif key == "days_on_market":
			self.days_on_market = _integer(value)
		else:
			self.extra[key] = value

	def __delitem__(self, key: str) -> None:
		if key not in self.extra:
			raise KeyError(key)
		del self.extra[key]

	def __eq__(self, other: object) -> bool:
		if isinstance(other, Listing):
			return self.to_legacy_dict() == other.to_legacy_dict()
		if isinstance(other, Mapping):
			ours = self.to_legacy_dict()
			return all(ours.get(key) == value for key, value in other.items())
		return NotImplemented

	@classmethod
	def from_legacy(cls, value: Mapping[str, Any]) -> "Listing":
		return listing_from_legacy(value)


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
		"supplementary_status",
	}
	addons = _dict(data.get("installed_addons"))
	return Listing(
		id=str(data.get("id") or data.get("vin") or ""),
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
			dealer_fees=list(seller_data.get("dealer_fees") or []),
		),
		documents=ListingDocuments(
			carfax_url=_string(documents.get("carfax_url")),
			autocheck_url=_string(documents.get("autocheck_url")),
			window_sticker_url=_string(documents.get("window_sticker_url")),
		),
		images=list(data.get("images") or []),
		installed_options=addons.get("items"),
		installed_options_total=_decimal(addons.get("total")),
		price_history=data.get("price_history"),
		warranty_coverages=list(warranty.get("coverages") or []),
		source=str(source_data.get("provider") or "legacy"),
		source_record_id=str(data.get("id")) if data.get("id") is not None else None,
		provenance=_dict(data.get("provenance")),
		warnings=list(data.get("warnings") or []),
		supplementary_status=SupplementaryStatus.from_dict(
			_dict(data.get("supplementary_status"))
		),
		raw_source=source_data,
		extra={key: item for key, item in data.items() if key not in known},
	)


def _dict(value: Any) -> dict[str, Any]:
	return dict(value) if isinstance(value, Mapping) else {}


def _string(value: Any) -> str | None:
	return str(value) if value is not None else None


def _integer(value: Any) -> int | None:
	if value is None or isinstance(value, bool):
		return None
	try:
		return int(value)
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
		"name": value.name,
		"location": value.location,
		"phone": value.phone,
		"stock_number": value.stock_number,
	}
	if value.dealer_fees:
		result["dealer_fees"] = value.dealer_fees
	return result
