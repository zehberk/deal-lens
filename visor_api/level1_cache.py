"""Atomic, end-of-day caching for Level 1 facet responses."""

import hashlib
import json

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from visor_api.client import QueryParams
from visor_api.level1_query import (
	Level1FacetQuery,
	build_level1_facet_query_plan,
	build_level1_trim_enrichment_query_plan,
)
from visor_api.level1_service import (
	Level1FacetCollection,
	RetrievedLevel1Facet,
	assemble_level1_facets,
)
from visor_api.models import FacetResponse
from visor_api.query import VisorListingQuery


LEVEL1_CACHE_SCHEMA_VERSION = 2


class CachedFacetClient(Protocol):
	def filter_facets_model_with_headers(
		self, params: QueryParams | None = None
	) -> tuple[FacetResponse, dict[str, str]]: ...


@dataclass(frozen=True, kw_only=True)
class CachedLevel1FacetResult:
	collection: Level1FacetCollection
	cache_path: Path
	cache_used: bool


def cached_level1_facets(
	client: CachedFacetClient,
	query: VisorListingQuery,
	*,
	cache_dir: str | Path,
	force: bool = False,
	clock: Callable[[], datetime] | None = None,
) -> CachedLevel1FacetResult:
	"""Return a complete Level 1 collection cached through the local day."""
	if query.unsupported:
		raise ValueError(f"unsupported query options: {sorted(query.unsupported)}")
	now = clock or (lambda: datetime.now(timezone.utc))
	cache_date = _local_date(now())
	initial_plan = build_level1_facet_query_plan(query)
	initial_queries = {_query_fingerprint(item): item for item in initial_plan}
	plan_fingerprint = _hash("|".join(sorted(initial_queries)))
	cache_path = Path(cache_dir) / f"visor-level1-{plan_fingerprint}.json"

	if cache_path.is_file() and not force:
		envelope = _read_cache(cache_path, cache_date, plan_fingerprint)
		initial_cached = _load_entries(envelope, initial_queries)
		if initial_cached is not None:
			initial_collection = assemble_level1_facets(initial_cached)
			enrichment_plan = build_level1_trim_enrichment_query_plan(
				query, _trims_by_year(initial_collection)
			)
			all_queries = _query_map((*initial_plan, *enrichment_plan))
			cached = _load_entries(envelope, all_queries, require_exact=True)
			if cached is not None:
				return CachedLevel1FacetResult(
					collection=assemble_level1_facets(cached),
					cache_path=cache_path,
					cache_used=True,
				)

	responses, entries = _fetch_queries(client, initial_queries, now)
	initial_collection = assemble_level1_facets(tuple(responses))
	enrichment_plan = build_level1_trim_enrichment_query_plan(
		query, _trims_by_year(initial_collection)
	)
	enrichment_responses, enrichment_entries = _fetch_queries(
		client, _query_map(enrichment_plan), now
	)
	responses.extend(enrichment_responses)
	entries.update(enrichment_entries)
	collection = assemble_level1_facets(tuple(responses))
	_write_cache(cache_path, {
		"cache_schema": LEVEL1_CACHE_SCHEMA_VERSION,
		"cache_date": cache_date,
		"plan_fingerprint": plan_fingerprint,
		"entries": entries,
	})
	return CachedLevel1FacetResult(
		collection=collection,
		cache_path=cache_path,
		cache_used=False,
	)


def _fetch_queries(
	client: CachedFacetClient,
	queries: dict[str, Level1FacetQuery],
	clock: Callable[[], datetime],
) -> tuple[list[RetrievedLevel1Facet], dict[str, dict[str, Any]]]:
	responses = []
	entries = {}
	for fingerprint, planned_query in queries.items():
		response, usage_headers = client.filter_facets_model_with_headers(
			planned_query.api_params()
		)
		retrieved_at = _aware_isoformat(clock())
		responses.append(RetrievedLevel1Facet(
			query=planned_query,
			response=response,
			retrieved_at=retrieved_at,
			usage_headers=usage_headers,
		))
		entries[fingerprint] = {
			"query": _json_query(planned_query.api_params()),
			"retrieved_at": retrieved_at,
			"usage_headers": usage_headers,
			"response": response.to_dict(),
		}
	return responses, entries


def _read_cache(
	cache_path: Path,
	cache_date: str,
	plan_fingerprint: str,
) -> dict[str, Any] | None:
	try:
		envelope = json.loads(cache_path.read_text(encoding="utf-8"))
		if (
			envelope.get("cache_schema") != LEVEL1_CACHE_SCHEMA_VERSION
			or envelope.get("cache_date") != cache_date
			or envelope.get("plan_fingerprint") != plan_fingerprint
			or not isinstance(envelope.get("entries"), dict)
		):
			return None
		return envelope
	except (OSError, TypeError, ValueError, json.JSONDecodeError):
		return None


def _load_entries(
	envelope: dict[str, Any] | None,
	queries: dict[str, Level1FacetQuery],
	*,
	require_exact: bool = False,
) -> tuple[RetrievedLevel1Facet, ...] | None:
	if envelope is None:
		return None
	try:
		entries = envelope["entries"]
		if require_exact and set(entries) != set(queries):
			return None
		responses = []
		for fingerprint, planned_query in queries.items():
			entry = entries[fingerprint]
			if entry["query"] != _json_query(planned_query.api_params()):
				return None
			responses.append(RetrievedLevel1Facet(
				query=planned_query,
				response=FacetResponse.from_dict(entry["response"]),
				retrieved_at=entry["retrieved_at"],
				usage_headers=dict(entry.get("usage_headers", {})),
			))
		return tuple(responses)
	except (KeyError, TypeError, ValueError):
		return None


def _write_cache(cache_path: Path, envelope: dict[str, Any]) -> None:
	cache_path.parent.mkdir(parents=True, exist_ok=True)
	temporary_path = cache_path.with_suffix(".tmp")
	temporary_path.write_text(
		json.dumps(envelope, indent=2, ensure_ascii=False),
		encoding="utf-8",
	)
	temporary_path.replace(cache_path)


def _trims_by_year(collection: Level1FacetCollection) -> dict[int, tuple[str, ...]]:
	return {
		year.year: tuple(bucket.trim for bucket in year.trims)
		for year in collection.years
	}


def _query_map(
	queries: Sequence[Level1FacetQuery],
) -> dict[str, Level1FacetQuery]:
	return {_query_fingerprint(item): item for item in queries}


def _query_fingerprint(query: Level1FacetQuery) -> str:
	payload = {"endpoint": "/v1/facets", "query": _json_query(query.api_params())}
	return _hash(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _json_query(params: Mapping[str, object]) -> dict[str, Any]:
	result = {}
	for name, value in sorted(params.items()):
		result[name] = (
			list(value)
			if isinstance(value, Sequence) and not isinstance(value, str)
			else value
		)
	return result


def _hash(value: str) -> str:
	return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _aware_isoformat(value: datetime) -> str:
	if value.tzinfo is None or value.utcoffset() is None:
		raise ValueError("Level 1 retrieval clock must return an aware datetime")
	return value.isoformat()


def _local_date(value: datetime) -> str:
	if value.tzinfo is None or value.utcoffset() is None:
		raise ValueError("Level 1 cache clock must return an aware datetime")
	return value.astimezone().date().isoformat()
