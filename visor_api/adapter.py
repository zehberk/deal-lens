"""Adapt Visor Public API payloads to the legacy DealLens listing contract."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from visor_api.models import APIModel


CONDITION_MAP = {
	"new": "New",
	"used": "Used",
	"certified": "Certified",
}

SPEC_FIELDS = {
	"version": "Trim Version",
	"body_type": "Body Style",
	"drivetrain": "Drivetrain",
	"fuel_type": "Fuel Type",
	"powertrain_type": "Powertrain Type",
	"transmission": "Transmission",
	"engine": "Engine",
	"cylinders": "Cylinders",
	"doors": "Doors",
	"seating_capacity": "Seating Capacity",
	"exterior_color": "Exterior Color",
	"interior_color": "Interior Color",
	"base_exterior_color": "Base Exterior Color",
	"base_interior_color": "Base Interior Color",
	"assembly_location": "Assembly Location",
}


def _mapping(value: Any) -> Mapping[str, Any]:
	if isinstance(value, APIModel):
		return value.to_dict()
	return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any] | None:
	if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
		return list(value)
	return None


def _first(*values: Any) -> Any:
	return next((value for value in values if value is not None), None)


def _is_number(value: Any) -> bool:
	return isinstance(value, (int, float)) and not isinstance(value, bool)


def _warning(
	field: str,
	code: str,
	message: str,
	*,
	api_path: str | None = None,
	received: Any = None,
) -> dict[str, Any]:
	result = {
		"code": code,
		"field": field,
		"message": message,
		"source": "visor_api",
	}
	if api_path is not None:
		result["api_path"] = api_path
	if received is not None:
		result["received_type"] = type(received).__name__
	return result


def _source_value(
	search: Mapping[str, Any], detail: Mapping[str, Any], build: Mapping[str, Any], key: str
) -> tuple[Any, str | None]:
	if build.get(key) is not None:
		return build[key], f"vehicle.build.{key}"
	if detail.get(key) is not None:
		return detail[key], key
	if search.get(key) is not None:
		return search[key], key
	return None, None


def _location(dealer: Mapping[str, Any]) -> str | None:
	city = dealer.get("city")
	state = dealer.get("state")
	postal_code = dealer.get("postal_code")
	city_state = ", ".join(str(value) for value in (city, state) if value)
	return " ".join(value for value in (city_state, str(postal_code) if postal_code else "") if value) or None


def _adapt_options(
	value: Any,
) -> tuple[list[dict[str, Any]] | None, int | float | None]:
	options = _sequence(value)
	if options is None:
		return None, None
	items: list[dict[str, Any]] = []
	prices: list[int | float] = []
	for raw_option in options:
		option = _mapping(raw_option)
		price = option.get("msrp")
		item = {"name": option.get("name"), "price": price}
		if "code" in option:
			item["code"] = option.get("code")
		items.append(item)
		if isinstance(price, (int, float)) and not isinstance(price, bool):
			prices.append(price)
	return items, sum(prices)


def _adapt_price_history(value: Any) -> list[dict[str, Any]] | None:
	history = _sequence(value)
	if history is None:
		return None
	result = []
	for raw_entry in history:
		entry = _mapping(raw_entry)
		price_before = entry.get("price_before")
		price_after = entry.get("price_after")
		price_change = _first(entry.get("price_change"), entry.get("change"))
		if (
			price_change is None
			and isinstance(price_before, (int, float))
			and not isinstance(price_before, bool)
			and isinstance(price_after, (int, float))
			and not isinstance(price_after, bool)
		):
			price_change = price_after - price_before
		adapted_entry = {
			"date": _first(
				entry.get("timestamp"),
				entry.get("recorded_at"),
				entry.get("changed_at"),
				entry.get("date"),
			),
			"price": _first(entry.get("price"), price_after),
			"price_change": price_change,
		}
		for target, source in (
			("mileage", "miles"),
			("price_before", "price_before"),
			("price_after", "price_after"),
		):
			if source in entry:
				adapted_entry[target] = entry.get(source)
		result.append(adapted_entry)
	return result


def adapt_listing(
	search_listing: Mapping[str, Any] | APIModel | None,
	detail_listing: Mapping[str, Any] | APIModel | None = None,
) -> dict[str, Any]:
	"""Combine a search row and optional detail object into one legacy listing.

	The original source objects are retained verbatim under ``source_data``. Detail
	values take precedence when present; missing scalar facts remain ``None``.
	"""
	search = _mapping(search_listing)
	detail = _mapping(detail_listing)
	vehicle = _mapping(detail.get("vehicle"))
	build = _mapping(vehicle.get("build"))
	dealer = _mapping(detail.get("dealer"))
	if not dealer:
		dealer = {
			"dealer_id": search.get("dealer_id"),
			"name": search.get("dealer_name"),
			"city": search.get("city"),
			"state": search.get("state"),
			"postal_code": search.get("postal_code"),
			"latitude": search.get("latitude"),
			"longitude": search.get("longitude"),
		}

	provenance: dict[str, dict[str, Any]] = {}
	warnings: list[dict[str, Any]] = []

	def fact(legacy_path: str, key: str) -> Any:
		value, source_path = _source_value(search, detail, build, key)
		provenance[legacy_path] = (
			{"kind": "source_fact", "api_path": source_path}
			if source_path
			else {"kind": "unavailable", "reason": "not_provided_by_api"}
		)
		return value

	year = fact("year", "year")
	make = fact("metadata.vehicle.make", "make")
	model = fact("metadata.vehicle.model", "model")
	trim = fact("trim", "trim")
	title_parts = [str(value) for value in (year, make, model, trim) if value is not None]
	title = " ".join(title_parts) or None
	provenance["title"] = {
		"kind": "calculated",
		"rule": "join non-null year, make, model, and trim with spaces",
		"inputs": ["year", "make", "model", "trim"],
	}

	inventory_type = _first(detail.get("inventory_type"), search.get("inventory_type"))
	condition = CONDITION_MAP.get(str(inventory_type).lower()) if inventory_type is not None else None
	provenance["condition"] = (
		{"kind": "calculated", "api_path": "inventory_type", "rule": "inventory type label mapping"}
		if condition is not None
		else {
			"kind": "unavailable",
			"reason": "unknown_inventory_type" if inventory_type is not None else "not_provided_by_api",
			"source_value": inventory_type,
		}
	)

	options_source = _first(build.get("options"), detail.get("options"), search.get("options"))
	option_items, option_total = _adapt_options(options_source)
	if options_source is None:
		warnings.append(_warning(
			"installed_addons.items",
			"missing_data",
			"Installed options were not requested or provided.",
			api_path="vehicle.build.options/options",
		))
	elif option_items is None:
		warnings.append(_warning(
			"installed_addons.items",
			"incompatible_data",
			"Installed options must be an array.",
			api_path="vehicle.build.options/options",
			received=options_source,
		))
	provenance["installed_addons.items"] = {
		"kind": "source_fact",
		"api_path": "vehicle.build.options/options",
	} if option_items is not None else {"kind": "unavailable", "reason": "not_requested_or_not_provided"}
	provenance["installed_addons.total"] = {
		"kind": "calculated",
		"rule": "sum non-null option msrp values",
		"inputs": ["vehicle.build.options[].msrp", "options[].msrp"],
	} if option_items is not None else {"kind": "unavailable", "reason": "not_requested_or_not_provided"}

	specs = {label: fact(f'specs["{label}"]', key) for key, label in SPEC_FIELDS.items()}
	listed = _first(detail.get("inventory_date"), detail.get("listed_at"), search.get("listed_at"))
	provenance["listed"] = {
		"kind": "source_fact",
		"api_path": "inventory_date" if detail.get("inventory_date") is not None else "listed_at",
	} if listed is not None else {"kind": "unavailable", "reason": "not_provided_by_api"}

	photos = _first(detail.get("photo_urls"), search.get("photo_urls"))
	images = _sequence(photos)
	if images is None:
		images = []
		warnings.append(_warning(
			"images",
			"missing_data" if photos is None else "incompatible_data",
			"Photo URLs were not provided." if photos is None else "Photo URLs must be an array.",
			api_path="photo_urls",
			received=photos,
		))
	provenance["images"] = (
		{"kind": "source_fact", "api_path": "photo_urls"}
		if photos is not None and _sequence(photos) is not None
		else {
			"kind": "unavailable",
			"reason": "not_provided_by_api" if photos is None else "incompatible_data",
		}
	)

	price_history_source = _first(detail.get("price_history"), search.get("price_history"))
	price_history = _adapt_price_history(price_history_source)
	if price_history_source is None:
		warnings.append(_warning(
			"price_history",
			"missing_data",
			"Price history was not requested or provided.",
			api_path="price_history",
		))
	elif price_history is None:
		warnings.append(_warning(
			"price_history",
			"incompatible_data",
			"Price history must be an array.",
			api_path="price_history",
			received=price_history_source,
		))
	provenance["price_history"] = {
		"kind": "source_fact",
		"api_path": "price_history",
	} if price_history is not None else {"kind": "unavailable", "reason": "not_requested_or_not_provided"}

	msrp = _first(build.get("combined_msrp"), search.get("msrp"), build.get("base_msrp"))
	if build.get("combined_msrp") is not None:
		msrp_path = "vehicle.build.combined_msrp"
	elif search.get("msrp") is not None:
		msrp_path = "msrp"
	elif build.get("base_msrp") is not None:
		msrp_path = "vehicle.build.base_msrp"
	else:
		msrp_path = None
	provenance["msrp"] = (
		{"kind": "source_fact", "api_path": msrp_path}
		if msrp_path else {"kind": "unavailable", "reason": "not_provided_by_api"}
	)

	days_on_market = search.get("days_on_market")
	provenance["days_on_market"] = (
		{"kind": "source_fact", "api_path": "days_on_market"}
		if days_on_market is not None
		else {"kind": "unavailable", "reason": "not_requested_or_not_provided"}
	)

	listing_id = _first(detail.get("id"), search.get("id"))
	vin = _first(detail.get("vin"), vehicle.get("vin"), search.get("vin"))
	price = _first(detail.get("price"), search.get("price"))
	mileage = _first(detail.get("miles"), search.get("miles"))
	listing_url = _first(detail.get("vdp_url"), search.get("vdp_url"))
	for field_name, value, api_path, expected in (
		("id", listing_id, "id", "string-compatible identifier"),
		("vin", vin, "vin", "string"),
		("year", year, "vehicle.build.year/year", "number"),
		("metadata.vehicle.make", make, "vehicle.build.make/make", "string"),
		("metadata.vehicle.model", model, "vehicle.build.model/model", "string"),
		("trim", trim, "vehicle.build.trim/trim", "string"),
		("msrp", msrp, msrp_path or "msrp", "number"),
		("price", price, "price", "number"),
		("mileage", mileage, "miles", "number"),
		("days_on_market", days_on_market, "days_on_market", "number"),
		("listing_url", listing_url, "vdp_url", "string"),
		("seller.name", dealer.get("name"), "dealer.name/dealer_name", "string"),
	):
		if value is None:
			warnings.append(_warning(
				field_name,
				"missing_data",
				f"{field_name} was not provided.",
				api_path=api_path,
			))
		elif (
			(expected == "number" and not _is_number(value))
			or (expected == "string" and not isinstance(value, str))
			or (
				expected == "string-compatible identifier"
				and isinstance(value, (Mapping, Sequence))
				and not isinstance(value, (str, bytes, bytearray))
			)
		):
			warnings.append(_warning(
				field_name,
				"incompatible_data",
				f"{field_name} must be a {expected}.",
				api_path=api_path,
				received=value,
			))

	listing = {
		"id": str(listing_id) if listing_id is not None else None,
		"vin": vin,
		"title": title,
		"year": year,
		"trim": trim,
		"condition": condition,
		"msrp": msrp,
		"price": price,
		"mileage": mileage,
		"days_on_market": days_on_market,
		"listed": listed,
		"listing_url": listing_url,
		"images": images,
		"seller": {
			"name": dealer.get("name"),
			"location": _location(dealer),
			"phone": dealer.get("phone"),
			"stock_number": _first(detail.get("stock_number"), search.get("stock_number")),
		},
		"specs": specs,
		"installed_addons": {"items": option_items, "total": option_total},
		"price_history": price_history,
		"additional_docs": {
			"carfax_url": None,
			"autocheck_url": None,
			"window_sticker_url": None,
		},
		"warranty": None,
		"market_velocity": None,
		"source_data": {
			"provider": "visor_api",
			"search_listing": deepcopy(search),
			"detail_listing": deepcopy(detail),
		},
		"provenance": provenance,
		"warnings": warnings,
	}
	for legacy_path, api_key in (
		("id", "id"), ("vin", "vin"), ("price", "price"), ("mileage", "miles"),
		("listing_url", "vdp_url"), ("seller.name", "name"),
		("seller.phone", "phone"), ("seller.stock_number", "stock_number"),
	):
		if legacy_path not in provenance:
			provenance[legacy_path] = {"kind": "source_fact", "api_path": api_key}

	for path in ("additional_docs.carfax_url", "additional_docs.autocheck_url", "additional_docs.window_sticker_url", "warranty", "market_velocity"):
		provenance[path] = {"kind": "unavailable", "reason": "not_provided_by_api"}
	return listing


def adapt_facets_response(
	response: Mapping[str, Any] | APIModel,
	*,
	request_filters: Mapping[str, Any] | None = None,
	captured_at: str | None = None,
) -> dict[str, Any]:
	"""Preserve a facet result separately from individual listing records."""
	response_data = _mapping(response)
	data = _mapping(response_data.get("data"))
	return {
		"total": data.get("total"),
		"facets": deepcopy(data.get("facets")),
		"range_facets": deepcopy(data.get("range_facets")),
		"stats": deepcopy(data.get("stats")),
		"request_filters": deepcopy(dict(request_filters or {})),
		"captured_at": captured_at or datetime.now(timezone.utc).isoformat(),
		"source_data": deepcopy(response_data),
	}


def adapt_search_response(
	response: Mapping[str, Any] | APIModel,
	*,
	details: Mapping[str, Mapping[str, Any]] | None = None,
	request_filters: Mapping[str, Any] | None = None,
	facets_response: Mapping[str, Any] | None = None,
	trim_facets_responses: Mapping[str, Mapping[str, Any]] | None = None,
	source_metadata: Mapping[str, Any] | None = None,
	captured_at: str | None = None,
	facets_captured_at: str | None = None,
	trim_facets_captured_at: Mapping[str, str] | None = None,
) -> dict[str, Any]:
	"""Adapt a listing-search response to DealLens's metadata/listings envelope."""
	response_data = _mapping(response)
	rows = _sequence(response_data.get("data")) or []
	detail_by_id = details or {}
	listings = [adapt_listing(_mapping(row), detail_by_id.get(str(_mapping(row).get("id")))) for row in rows]
	warnings = []
	for listing in listings:
		for listing_warning in listing.get("warnings", []):
			warnings.append({
				**listing_warning,
				"listing_id": listing.get("id"),
				"vin": listing.get("vin"),
			})
	if _sequence(response_data.get("data")) is None:
		warnings.append(_warning(
			"listings",
			"missing_data" if response_data.get("data") is None else "incompatible_data",
			"Listing search data was not provided as an array.",
			api_path="data",
			received=response_data.get("data"),
		))
	first = listings[0] if listings else {}
	first_source = _mapping(_mapping(first.get("source_data")).get("search_listing"))
	metadata = {
		"vehicle": {
			"make": first_source.get("make"),
			"model": first_source.get("model"),
			"trim": first_source.get("trim"),
			"year": first_source.get("year"),
		},
		"filters": deepcopy(dict(request_filters or {})),
		"site_info": {},
		"runtime": {"timestamp": captured_at or datetime.now(timezone.utc).isoformat(), "source": "visor_api"},
		"warnings": warnings,
		"pagination": deepcopy(response_data.get("pagination")),
	}
	if source_metadata is not None:
		metadata["sources"] = {"visor_api": deepcopy(dict(source_metadata))}
	result = {"metadata": metadata, "listings": listings, "source_data": {"listing_search": deepcopy(response_data)}}
	if facets_response is not None:
		facet_result = adapt_facets_response(
			facets_response,
			request_filters=request_filters,
			captured_at=facets_captured_at or captured_at,
		)
		result["facet_result"] = facet_result
		metadata["site_info"] = {
			"total_for_sale": facet_result["total"],
			"facets": facet_result["facets"],
			"range_facets": facet_result["range_facets"],
			"stats": facet_result["stats"],
		}
		overall_stats = _mapping(facet_result.get("stats")).get("days_on_market")
		trim_facet_results = {}
		trim_stats = {}
		for trim, trim_response in (trim_facets_responses or {}).items():
			trim_filters = {**dict(request_filters or {}), "trim": [trim]}
			trim_result = adapt_facets_response(
				trim_response,
				request_filters=trim_filters,
				captured_at=(trim_facets_captured_at or {}).get(trim)
				or facets_captured_at
				or captured_at,
			)
			trim_facet_results[trim] = trim_result
			trim_stats[trim] = _mapping(trim_result.get("stats")).get("days_on_market")
		metadata["site_info"]["days_on_market"] = {
			"overall": deepcopy(overall_stats),
			"by_trim": deepcopy(trim_stats),
		}
		if trim_facet_results:
			result["trim_facet_results"] = trim_facet_results
	return result
