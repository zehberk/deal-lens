"""Collect enriched listing search data and standard detail for Level 2."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from visor_api.adapter import adapt_listing
from visor_api.client import QueryParams, VisorAPIError, VisorTimeoutError
from visor_api.query import VisorListingQuery


LEVEL2_SEARCH_FIELDS = (
	"default,msrp,discount_from_msrp,days_on_market,photo_urls,features,"
	"options_packages"
)
LEVEL2_SEARCH_EXPANSIONS = "options,price_history"


class Level2Client(Protocol):
	"""Client capabilities required by Level 2 collection."""

	def filter_all_listings(
		self,
		params: QueryParams | None = None,
		*,
		max_listings: int = 50,
	) -> dict[str, Any]: ...

	def get_listing(
		self,
		listing_id: str,
		params: QueryParams | None = None,
	) -> dict[str, Any]: ...


@dataclass(frozen=True, kw_only=True)
class Level2ListingRecord:
	"""One retained search record plus optional detail and adapted data."""

	listing_id: str
	vin: str | None
	listing: dict[str, Any]
	search_record: dict[str, Any]
	detail_record: dict[str, Any] | None
	detail_error: str | None = None

	def to_dict(self) -> dict[str, Any]:
		return {
			"listing_id": self.listing_id,
			"vin": self.vin,
			"listing": self.listing,
			"search_record": self.search_record,
			"detail_record": self.detail_record,
			"detail_error": self.detail_error,
		}

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> "Level2ListingRecord":
		listing_id = value.get("listing_id")
		if not isinstance(listing_id, str) or not listing_id:
			raise ValueError("cached Level 2 listing_id must be a non-empty string")
		return cls(
			listing_id=listing_id,
			vin=_optional_string(value.get("vin")),
			listing=_dictionary(value.get("listing"), "listing"),
			search_record=_dictionary(value.get("search_record"), "search_record"),
			detail_record=(
				_dictionary(value.get("detail_record"), "detail_record")
				if value.get("detail_record") is not None else None
			),
			detail_error=_optional_string(value.get("detail_error")),
		)


@dataclass(frozen=True, kw_only=True)
class Level2Exclusion:
	"""A search row that could not become a stable Level 2 listing."""

	index: int
	reason: str
	listing_id: str | None = None
	vin: str | None = None
	received_type: str | None = None

	def to_dict(self) -> dict[str, Any]:
		return {
			"index": self.index,
			"reason": self.reason,
			"listing_id": self.listing_id,
			"vin": self.vin,
			"received_type": self.received_type,
		}

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> "Level2Exclusion":
		return cls(
			index=int(value["index"]),
			reason=str(value["reason"]),
			listing_id=_optional_string(value.get("listing_id")),
			vin=_optional_string(value.get("vin")),
			received_type=_optional_string(value.get("received_type")),
		)


@dataclass(frozen=True, kw_only=True)
class Level2Collection:
	"""Complete Level 2 acquisition result with raw source provenance."""

	listings: tuple[Level2ListingRecord, ...]
	exclusions: tuple[Level2Exclusion, ...]
	request_params: dict[str, Any]
	retrieved_at: str
	raw_search_response: dict[str, Any]

	def to_dict(self) -> dict[str, Any]:
		return {
			"listings": [item.to_dict() for item in self.listings],
			"exclusions": [item.to_dict() for item in self.exclusions],
			"request_params": self.request_params,
			"retrieved_at": self.retrieved_at,
			"raw_search_response": self.raw_search_response,
		}

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> "Level2Collection":
		listings = value.get("listings")
		exclusions = value.get("exclusions")
		if not isinstance(listings, list) or not isinstance(exclusions, list):
			raise ValueError("cached Level 2 collection arrays are invalid")
		return cls(
			listings=tuple(Level2ListingRecord.from_dict(_mapping(item)) for item in listings),
			exclusions=tuple(Level2Exclusion.from_dict(_mapping(item)) for item in exclusions),
			request_params=_dictionary(value.get("request_params"), "request_params"),
			retrieved_at=str(value["retrieved_at"]),
			raw_search_response=_dictionary(
				value.get("raw_search_response"), "raw_search_response"
			),
		)


def collect_level2_listings(
	client: Level2Client,
	query: VisorListingQuery,
	*,
	max_listings: int = 100,
	clock: Callable[[], datetime] | None = None,
) -> Level2Collection:
	"""Fetch an enriched search and standard detail sequentially for Level 2."""
	if query.unsupported:
		raise ValueError(f"unsupported query options: {sorted(query.unsupported)}")
	if max_listings < 0:
		raise ValueError("max_listings must not be negative")
	now = clock or (lambda: datetime.now(timezone.utc))
	request_params = level2_search_params(query)
	response = client.filter_all_listings(
		request_params,
		max_listings=max_listings,
	)
	rows = response.get("data")
	if not isinstance(rows, list):
		raise ValueError("Level 2 listing search data must be an array")

	listings: list[Level2ListingRecord] = []
	exclusions: list[Level2Exclusion] = []
	seen_ids: set[str] = set()
	for index, raw_row in enumerate(rows):
		if not isinstance(raw_row, Mapping):
			exclusions.append(Level2Exclusion(
				index=index,
				reason="search_record_not_object",
				received_type=type(raw_row).__name__,
			))
			continue
		row = dict(raw_row)
		listing_id = _optional_string(row.get("id"))
		vin = _optional_string(row.get("vin"))
		if listing_id is None:
			exclusions.append(Level2Exclusion(
				index=index,
				reason="missing_stable_listing_id",
				vin=vin,
			))
			continue
		if listing_id in seen_ids:
			exclusions.append(Level2Exclusion(
				index=index,
				reason="duplicate_stable_listing_id",
				listing_id=listing_id,
				vin=vin,
			))
			continue
		seen_ids.add(listing_id)

		detail, detail_error = _collect_detail(client, listing_id)
		adapted = adapt_listing(row, detail)
		if detail_error is not None:
			adapted.setdefault("warnings", []).append({
				"field": "detail",
				"code": "source_error",
				"message": "Listing detail could not be retrieved.",
				"api_path": f"/v1/listings/{listing_id}",
			})
			adapted.setdefault("provenance", {})["detail"] = {
				"kind": "unavailable",
				"reason": "source_error",
			}
		listings.append(Level2ListingRecord(
			listing_id=listing_id,
			vin=vin,
			listing=adapted,
			search_record=row,
			detail_record=detail,
			detail_error=detail_error,
		))

	return Level2Collection(
		listings=tuple(listings),
		exclusions=tuple(exclusions),
		request_params=_json_values(request_params),
		retrieved_at=_aware_isoformat(now()),
		raw_search_response=dict(response),
	)


def level2_search_params(query: VisorListingQuery) -> dict[str, Any]:
	"""Return the enriched search parameters selected for Level 2."""
	return {
		**query.api_params(include_projection=False),
		"fields": LEVEL2_SEARCH_FIELDS,
		"include": LEVEL2_SEARCH_EXPANSIONS,
	}


def _collect_detail(
	client: Level2Client,
	listing_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
	try:
		response = client.get_listing(listing_id)
	except (VisorAPIError, VisorTimeoutError) as error:
		return None, str(error)
	except Exception as error:
		return None, type(error).__name__
	data = response.get("data")
	if not isinstance(data, Mapping):
		return None, "invalid_detail_response"
	return dict(data), None


def _json_values(value: Mapping[str, Any]) -> dict[str, Any]:
	return {
		name: list(item) if isinstance(item, tuple) else item
		for name, item in value.items()
	}


def _aware_isoformat(value: datetime) -> str:
	if value.tzinfo is None or value.utcoffset() is None:
		raise ValueError("Level 2 retrieval clock must return an aware datetime")
	return value.isoformat()


def _optional_string(value: Any) -> str | None:
	if value is None:
		return None
	result = str(value).strip()
	return result or None


def _mapping(value: Any) -> Mapping[str, Any]:
	if not isinstance(value, Mapping):
		raise ValueError("cached Level 2 item must be an object")
	return value


def _dictionary(value: Any, name: str) -> dict[str, Any]:
	if not isinstance(value, Mapping):
		raise ValueError(f"cached Level 2 {name} must be an object")
	return dict(value)
