"""Deterministic local caching for Visor listing searches."""

import hashlib
import json

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from visor_api.adapter import adapt_search_response
from visor_api.query import VisorListingQuery
from visor_api.models import FacetResponse, ListingSearchResponse


CACHE_SCHEMA_VERSION = 3


class ListingSearchClient(Protocol):
	"""Client behavior required by the listing cache boundary."""

	def filter_all_listings_model(
		self,
		params: dict[str, str | tuple[str, ...]],
		*,
		max_listings: int,
	) -> ListingSearchResponse: ...

	def filter_facets_model(
		self,
		params: dict[str, str | tuple[str, ...]],
	) -> FacetResponse: ...


@dataclass(frozen=True)
class CachedSearchResult:
	"""A listing response and whether it came from the local cache."""

	response: ListingSearchResponse
	facets_response: FacetResponse
	trim_facets_responses: dict[str, FacetResponse]
	payload: dict[str, Any]
	metadata: dict[str, Any]
	cache_path: Path
	cache_used: bool


def cached_listing_search(
	client: ListingSearchClient,
	query: VisorListingQuery,
	*,
	cache_dir: str | Path,
	max_listings: int = 10,
	force: bool = False,
	include_projection: bool = False,
) -> CachedSearchResult:
	"""Return a cached search or fetch and atomically replace its cache file.

	Optional enriched fields and expansions are excluded by default to minimize API
	usage cost. Callers must opt in with ``include_projection=True``.
	"""
	if max_listings <= 0:
		raise ValueError("max_listings must be greater than zero")
	if query.unsupported:
		raise ValueError(f"unsupported query options: {sorted(query.unsupported)}")

	request_params = query.api_params(include_projection=include_projection)
	market_filters = query.market_filters()
	selected_trims = tuple(market_filters.get("trim", ()))
	fingerprint = query.fingerprint(
		max_listings,
		include_projection=include_projection,
	)
	cache_path = Path(cache_dir) / f"visor-listings-{_cache_key(fingerprint)}.json"
	if cache_path.is_file() and not force:
		envelope = json.loads(cache_path.read_text(encoding="utf-8"))
		cached_trim_facets = envelope.get("trim_facets_responses", {})
		if (
			"facets_response" in envelope
			and envelope.get("metadata", {}).get("cache_schema")
			== CACHE_SCHEMA_VERSION
			and set(cached_trim_facets) == set(selected_trims)
		):
			response = ListingSearchResponse.from_dict(envelope["response"])
			facets_response = FacetResponse.from_dict(envelope["facets_response"])
			trim_facets_responses = {
				trim: FacetResponse.from_dict(trim_response)
				for trim, trim_response in cached_trim_facets.items()
			}
			metadata = envelope["metadata"]
			return CachedSearchResult(
				response=response,
				facets_response=facets_response,
				trim_facets_responses=trim_facets_responses,
				payload=_adapt_payload(
					response, facets_response, trim_facets_responses, metadata
				),
				metadata=metadata,
				cache_path=cache_path,
				cache_used=True,
			)

	response = client.filter_all_listings_model(
		request_params,
		max_listings=max_listings,
	)
	listing_retrieved_at = datetime.now(timezone.utc).isoformat()
	facet_params = {
		**market_filters,
		"facets": "model,trim,days_on_market",
	}
	facets_response = client.filter_facets_model(facet_params)
	facet_retrieved_at = datetime.now(timezone.utc).isoformat()
	trim_facet_params = {
		trim: {**market_filters, "trim": (trim,), "facets": "days_on_market"}
		for trim in selected_trims
	}
	trim_facets_responses = {}
	trim_facets_retrieved_at = {}
	for trim, params in trim_facet_params.items():
		trim_facets_responses[trim] = client.filter_facets_model(params)
		trim_facets_retrieved_at[trim] = datetime.now(timezone.utc).isoformat()
	metadata = {
		"provider": "visor_api",
		"cache_schema": CACHE_SCHEMA_VERSION,
		"fingerprint": fingerprint,
		"query": _json_query(request_params),
		"market_filters": _json_query(market_filters),
		"facet_query": _json_query(facet_params),
		"trim_facet_queries": {
			trim: _json_query(params)
			for trim, params in trim_facet_params.items()
		},
		"max_listings": max_listings,
		"listing_retrieved_at": listing_retrieved_at,
		"facet_retrieved_at": facet_retrieved_at,
		"trim_facets_retrieved_at": trim_facets_retrieved_at,
		"fetched_at": listing_retrieved_at,
	}
	envelope = {
		"metadata": metadata,
		"response": response.to_dict(),
		"facets_response": facets_response.to_dict(),
		"trim_facets_responses": {
			trim: trim_response.to_dict()
			for trim, trim_response in trim_facets_responses.items()
		},
	}
	cache_path.parent.mkdir(parents=True, exist_ok=True)
	temporary_path = cache_path.with_suffix(".tmp")
	temporary_path.write_text(
		json.dumps(envelope, indent=2, ensure_ascii=False),
		encoding="utf-8",
	)
	temporary_path.replace(cache_path)
	return CachedSearchResult(
		response=response,
		facets_response=facets_response,
		trim_facets_responses=trim_facets_responses,
		payload=_adapt_payload(
			response, facets_response, trim_facets_responses, metadata
		),
		metadata=metadata,
		cache_path=cache_path,
		cache_used=False,
	)


def _adapt_payload(
	response: ListingSearchResponse,
	facets_response: FacetResponse,
	trim_facets_responses: dict[str, FacetResponse],
	metadata: dict[str, Any],
) -> dict[str, Any]:
	market_filters = metadata.get("market_filters")
	if market_filters is None:
		market_filters = {
			name: value
			for name, value in metadata["facet_query"].items()
			if name != "facets"
		}
	return adapt_search_response(
		response,
		request_filters=market_filters,
		facets_response=facets_response.to_dict(),
		trim_facets_responses={
			trim: trim_response.to_dict()
			for trim, trim_response in trim_facets_responses.items()
		},
		source_metadata=_source_metadata(metadata),
		captured_at=metadata["fetched_at"],
		facets_captured_at=metadata["facet_retrieved_at"],
		trim_facets_captured_at=metadata["trim_facets_retrieved_at"],
	)


def _source_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
	return {
		"listings": {
			"endpoint": "/v1/listings",
			"query": metadata["query"],
			"max_listings": metadata["max_listings"],
			"retrieved_at": metadata["listing_retrieved_at"],
		},
		"facets": {
			"overall": {
				"endpoint": "/v1/facets",
				"query": metadata["facet_query"],
				"retrieved_at": metadata["facet_retrieved_at"],
			},
			"by_trim": {
				trim: {
					"endpoint": "/v1/facets",
					"query": query,
					"retrieved_at": metadata["trim_facets_retrieved_at"][trim],
				}
				for trim, query in metadata["trim_facet_queries"].items()
			},
		},
	}


def _cache_key(fingerprint: str) -> str:
	return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]


def _json_query(params: dict[str, str | tuple[str, ...]]) -> dict[str, Any]:
	return {
		name: list(value) if isinstance(value, tuple) else value
		for name, value in params.items()
	}
