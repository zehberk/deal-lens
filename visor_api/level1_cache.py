"""Atomic, query-complete caching for Level 1 facet responses."""

import hashlib
import json

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from visor_api.client import QueryParams
from visor_api.level1_query import Level1FacetQuery, build_level1_facet_query_plan
from visor_api.level1_service import (
	Level1FacetCollection,
	RetrievedLevel1Facet,
	assemble_level1_facets,
)
from visor_api.models import FacetResponse
from visor_api.query import VisorListingQuery


LEVEL1_CACHE_SCHEMA_VERSION = 1


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
	"""Return one coherent cached Level 1 facet collection.

	Each response is keyed by its complete endpoint and query. The containing
	envelope is replaced atomically only after every required response succeeds.
	"""
	if query.unsupported:
		raise ValueError(f"unsupported query options: {sorted(query.unsupported)}")
	plan = build_level1_facet_query_plan(query)
	fingerprints = {_query_fingerprint(item): item for item in plan}
	plan_fingerprint = _hash("|".join(sorted(fingerprints)))
	cache_path = Path(cache_dir) / f"visor-level1-{plan_fingerprint}.json"

	if cache_path.is_file() and not force:
		cached = _load_cache(cache_path, fingerprints)
		if cached is not None:
			return CachedLevel1FacetResult(
				collection=cached,
				cache_path=cache_path,
				cache_used=True,
			)

	now = clock or (lambda: datetime.now(timezone.utc))
	responses: list[RetrievedLevel1Facet] = []
	entries: dict[str, dict[str, Any]] = {}
	for fingerprint, planned_query in fingerprints.items():
		response, usage_headers = client.filter_facets_model_with_headers(
			planned_query.api_params()
		)
		retrieved_at = _aware_isoformat(now())
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

	collection = assemble_level1_facets(tuple(responses))
	envelope = {
		"cache_schema": LEVEL1_CACHE_SCHEMA_VERSION,
		"plan_fingerprint": plan_fingerprint,
		"entries": entries,
	}
	cache_path.parent.mkdir(parents=True, exist_ok=True)
	temporary_path = cache_path.with_suffix(".tmp")
	temporary_path.write_text(
		json.dumps(envelope, indent=2, ensure_ascii=False),
		encoding="utf-8",
	)
	temporary_path.replace(cache_path)
	return CachedLevel1FacetResult(
		collection=collection,
		cache_path=cache_path,
		cache_used=False,
	)


def _load_cache(
	cache_path: Path,
	queries: dict[str, Level1FacetQuery],
) -> Level1FacetCollection | None:
	try:
		envelope = json.loads(cache_path.read_text(encoding="utf-8"))
		entries = envelope["entries"]
		if (
			envelope.get("cache_schema") != LEVEL1_CACHE_SCHEMA_VERSION
			or not isinstance(entries, dict)
			or set(entries) != set(queries)
		):
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
		return assemble_level1_facets(tuple(responses))
	except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
		return None


def _query_fingerprint(query: Level1FacetQuery) -> str:
	payload = {
		"endpoint": "/v1/facets",
		"query": _json_query(query.api_params()),
	}
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
