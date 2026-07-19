"""Deterministic local caching for Visor listing searches."""

import hashlib
import json

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from visor_api.query import VisorListingQuery


class ListingSearchClient(Protocol):
	"""Client behavior required by the listing cache boundary."""

	def filter_all_listings(
		self,
		params: dict[str, str | tuple[str, ...]],
		*,
		max_listings: int,
	) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CachedSearchResult:
	"""A listing response and whether it came from the local cache."""

	response: dict[str, Any]
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
) -> CachedSearchResult:
	"""Return a cached search or fetch and atomically replace its cache file."""
	if max_listings <= 0:
		raise ValueError("max_listings must be greater than zero")
	if query.unsupported:
		raise ValueError(f"unsupported query options: {sorted(query.unsupported)}")

	fingerprint = query.fingerprint(max_listings)
	cache_path = Path(cache_dir) / f"visor-listings-{_cache_key(fingerprint)}.json"
	if cache_path.is_file() and not force:
		envelope = json.loads(cache_path.read_text(encoding="utf-8"))
		return CachedSearchResult(
			response=envelope["response"],
			metadata=envelope["metadata"],
			cache_path=cache_path,
			cache_used=True,
		)

	response = client.filter_all_listings(
		query.api_params(),
		max_listings=max_listings,
	)
	metadata = {
		"provider": "visor_api",
		"fingerprint": fingerprint,
		"query": _json_query(query.api_params()),
		"max_listings": max_listings,
		"fetched_at": datetime.now(timezone.utc).isoformat(),
	}
	envelope = {"metadata": metadata, "response": response}
	cache_path.parent.mkdir(parents=True, exist_ok=True)
	temporary_path = cache_path.with_suffix(".tmp")
	temporary_path.write_text(
		json.dumps(envelope, indent=2, ensure_ascii=False),
		encoding="utf-8",
	)
	temporary_path.replace(cache_path)
	return CachedSearchResult(
		response=response,
		metadata=metadata,
		cache_path=cache_path,
		cache_used=False,
	)


def _cache_key(fingerprint: str) -> str:
	return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]


def _json_query(params: dict[str, str | tuple[str, ...]]) -> dict[str, Any]:
	return {
		name: list(value) if isinstance(value, tuple) else value
		for name, value in params.items()
	}
