"""Atomic local cache for complete Level 2 listing collections."""

import hashlib
import json

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from visor_api.level2_service import (
	Level2Client,
	Level2Collection,
	collect_level2_listings,
	level2_search_params,
)
from visor_api.query import VisorListingQuery


LEVEL2_CACHE_SCHEMA_VERSION = 1


@dataclass(frozen=True, kw_only=True)
class CachedLevel2Result:
	collection: Level2Collection
	cache_path: Path
	cache_used: bool


def cached_level2_collection(
	client: Level2Client,
	query: VisorListingQuery,
	*,
	cache_dir: str | Path,
	max_listings: int = 100,
	force: bool = False,
	clock: Callable[[], datetime] | None = None,
) -> CachedLevel2Result:
	"""Return a Level 2 collection cached through the local calendar day."""
	if query.unsupported:
		raise ValueError(f"unsupported query options: {sorted(query.unsupported)}")
	if max_listings < 0:
		raise ValueError("max_listings must not be negative")
	now = clock or (lambda: datetime.now(timezone.utc))
	current = now()
	cache_date = _local_date(current)
	fingerprint = _fingerprint(query, max_listings)
	cache_path = Path(cache_dir) / f"visor-level2-{fingerprint}.json"

	if cache_path.is_file() and not force:
		collection = _read_cache(cache_path, cache_date, fingerprint)
		if collection is not None:
			return CachedLevel2Result(
				collection=collection,
				cache_path=cache_path,
				cache_used=True,
			)

	collection = collect_level2_listings(
		client,
		query,
		max_listings=max_listings,
		clock=lambda: current,
	)
	_write_cache(cache_path, {
		"cache_schema": LEVEL2_CACHE_SCHEMA_VERSION,
		"cache_date": cache_date,
		"fingerprint": fingerprint,
		"collection": collection.to_dict(),
	})
	return CachedLevel2Result(
		collection=collection,
		cache_path=cache_path,
		cache_used=False,
	)


def _read_cache(
	cache_path: Path,
	cache_date: str,
	fingerprint: str,
) -> Level2Collection | None:
	try:
		envelope = json.loads(cache_path.read_text(encoding="utf-8"))
		if (
			envelope.get("cache_schema") != LEVEL2_CACHE_SCHEMA_VERSION
			or envelope.get("cache_date") != cache_date
			or envelope.get("fingerprint") != fingerprint
		):
			return None
		collection = envelope.get("collection")
		if not isinstance(collection, dict):
			return None
		return Level2Collection.from_dict(collection)
	except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
		return None


def _write_cache(cache_path: Path, envelope: dict[str, Any]) -> None:
	cache_path.parent.mkdir(parents=True, exist_ok=True)
	temporary_path = cache_path.with_suffix(".tmp")
	temporary_path.write_text(
		json.dumps(envelope, indent=2, ensure_ascii=False),
		encoding="utf-8",
	)
	temporary_path.replace(cache_path)


def _fingerprint(query: VisorListingQuery, max_listings: int) -> str:
	payload = {
		"endpoint": "/v1/listings",
		"query": level2_search_params(query),
		"max_listings": max_listings,
	}
	encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
	return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _local_date(value: datetime) -> str:
	if value.tzinfo is None or value.utcoffset() is None:
		raise ValueError("Level 2 cache clock must return an aware datetime")
	return value.astimezone().date().isoformat()
