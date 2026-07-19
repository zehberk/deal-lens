"""Translate DealLens search options into the public Visor API contract."""

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlparse


LISTING_FIELDS = (
	"default,msrp,discount_from_msrp,days_on_market,photo_urls,features,"
	"options_packages"
)
LISTING_EXPANSIONS = "price_history,options"

LEGACY_PARAMETER_NAMES = {
	"car_type": "inventory_type",
	"condition": "inventory_type",
	"price_min": "min_price",
	"price_max": "max_price",
	"miles_min": "min_mileage",
	"miles_max": "max_mileage",
	"zip": "postal_code",
}

GEO_BROWSER_PARAMETERS = frozenset({
	"geo_origin_kind",
	"geo_origin_value",
	"geo_mode",
	"geo_distance_value",
	"geo_distance_unit",
})

SORT_VALUES = {
	"cheapest": "price",
	"expensive": "-price",
	"newest": "days_on_market",
	"oldest": "-days_on_market",
	"lowest_miles": "miles",
	"highest_miles": "-miles",
	"lowest price": "price",
	"highest price": "-price",
	"newest": "days_on_market",
	"oldest": "-days_on_market",
	"lowest mileage": "miles",
	"highest mileage": "-miles",
}

SUPPORTED_PARAMETERS = frozenset({
	"make", "model", "trim", "year", "inventory_type", "min_price",
	"max_price", "min_mileage", "max_mileage", "postal_code", "radius",
	"state", "latitude", "longitude", "sort",
})
MULTI_VALUE_PARAMETERS = frozenset({
	"make", "model", "trim", "year", "inventory_type", "state",
})


def _clean_value(raw_value: Any) -> str:
	value = str(raw_value).strip()
	if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
		value = value[1:-1].strip()
	return value


def _values(raw_value: Any) -> tuple[str, ...]:
	if isinstance(raw_value, (list, tuple, set)):
		parts = raw_value
	else:
		parts = str(raw_value).split(",")
	return tuple(_clean_value(value) for value in parts if _clean_value(value))


def _normalize_condition(value: str) -> str:
	condition = value.strip().lower()
	return "certified" if condition in {"certified", "cpo"} else condition


@dataclass(frozen=True)
class VisorListingQuery:
	"""Normalized API query plus legacy options that could not be translated."""

	filters: dict[str, str | tuple[str, ...]] = field(default_factory=dict)
	unsupported: dict[str, str] = field(default_factory=dict)

	@classmethod
	def from_url(cls, url: str) -> "VisorListingQuery":
		"""Build a query from a legacy Visor browser filter/listing URL."""
		return cls.from_options(dict(parse_qsl(urlparse(url).query)))

	@classmethod
	def from_options(cls, options: dict[str, Any]) -> "VisorListingQuery":
		"""Map DealLens/browser option names to stable public API parameters."""
		filters: dict[str, str | tuple[str, ...]] = {}
		unsupported: dict[str, str] = {}
		geo_options = {
			name: _clean_value(value)
			for name, value in options.items()
			if name in GEO_BROWSER_PARAMETERS and value not in (None, "")
		}

		for legacy_name, raw_value in options.items():
			if raw_value is None or raw_value == "":
				continue
			if legacy_name in GEO_BROWSER_PARAMETERS:
				continue
			name = LEGACY_PARAMETER_NAMES.get(legacy_name, legacy_name)
			if name == "location":
				location = _clean_value(raw_value)
				if len(location) == 5 and location.isdigit():
					filters["postal_code"] = location
				else:
					unsupported[legacy_name] = location
				continue
			if name not in SUPPORTED_PARAMETERS:
				unsupported[legacy_name] = _clean_value(raw_value)
				continue
			if name == "sort":
				value = _clean_value(raw_value)
				filters[name] = SORT_VALUES.get(value.lower(), value)
				continue
			if name in MULTI_VALUE_PARAMETERS:
				values = _values(raw_value)
				if name == "inventory_type":
					values = tuple(_normalize_condition(value) for value in values)
				filters[name] = tuple(sorted(set(values), key=str.casefold))
				continue
			filters[name] = _clean_value(raw_value)

		origin_kind = geo_options.get("geo_origin_kind")
		origin_value = geo_options.get("geo_origin_value")
		if origin_kind or origin_value:
			if origin_kind == "postal_code" and origin_value:
				filters["postal_code"] = origin_value
			else:
				for name in ("geo_origin_kind", "geo_origin_value"):
					if name in geo_options:
						unsupported[name] = geo_options[name]

		geo_mode = geo_options.get("geo_mode")
		distance = geo_options.get("geo_distance_value")
		unit = geo_options.get("geo_distance_unit")
		if geo_mode or distance or unit:
			if geo_mode == "radius" and distance and unit in (None, "mi"):
				filters["radius"] = distance
			else:
				for name in (
					"geo_mode",
					"geo_distance_value",
					"geo_distance_unit",
				):
					if name in geo_options:
						unsupported[name] = geo_options[name]

		return cls(filters=filters, unsupported=unsupported)

	def api_params(self, *, include_projection: bool = True) -> dict[str, str | tuple[str, ...]]:
		"""Return request parameters, optionally including DealLens' projection."""
		params = dict(self.filters)
		if include_projection:
			params["fields"] = LISTING_FIELDS
			params["include"] = LISTING_EXPANSIONS
		return params

	def fingerprint(
		self,
		max_listings: int,
		*,
		include_projection: bool = True,
	) -> str:
		"""Return a deterministic cache fingerprint for equivalent API queries."""
		parts = []
		for name, value in sorted(
			self.api_params(include_projection=include_projection).items()
		):
			encoded = ",".join(value) if isinstance(value, tuple) else value
			parts.append(f"{name}={encoded}")
		return "&".join(parts) + f"|max={int(max_listings)}"
