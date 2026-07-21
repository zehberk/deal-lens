import json
import shutil
import uuid

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from visor_api import VisorListingQuery, cached_level2_collection


class CacheClient:
	def __init__(self):
		self.search_calls = 0
		self.detail_calls = 0

	def filter_all_listings(self, params=None, *, max_listings=50):
		self.search_calls += 1
		return {
			"data": [{
				"id": f"listing-{self.search_calls}",
				"vin": f"VIN{self.search_calls}",
				"year": 2024,
				"make": "Subaru",
				"model": "Crosstrek",
			}],
			"pagination": {
				"limit": max_listings,
				"offset": 0,
				"total": 1,
				"next_offset": None,
			},
			"meta": {},
		}

	def get_listing(self, listing_id, params=None):
		self.detail_calls += 1
		return {"data": {"id": listing_id, "vin": f"VIN{self.search_calls}"}}


def query():
	return VisorListingQuery.from_options({
		"make": "Subaru",
		"model": "Crosstrek",
		"year": (2024, 2025, 2026),
	})


@pytest.fixture
def cache_dir() -> Iterator[Path]:
	path = Path("cache") / "test-level2-cache" / uuid.uuid4().hex
	path.mkdir(parents=True)
	try:
		yield path
	finally:
		shutil.rmtree(path)


def test_level2_cache_hit_and_force_refresh(cache_dir):
	client = CacheClient()
	clock = lambda: datetime(2026, 7, 21, tzinfo=timezone.utc)

	first = cached_level2_collection(
		client, query(), cache_dir=cache_dir, clock=clock
	)
	second = cached_level2_collection(
		client, query(), cache_dir=cache_dir, clock=clock
	)
	forced = cached_level2_collection(
		client, query(), cache_dir=cache_dir, clock=clock, force=True
	)

	assert first.cache_used is False
	assert second.cache_used is True
	assert forced.cache_used is False
	assert second.collection == first.collection
	assert forced.collection != first.collection
	assert client.search_calls == 2
	assert client.detail_calls == 2
	assert first.cache_path == second.cache_path == forced.cache_path
	envelope = json.loads(first.cache_path.read_text(encoding="utf-8"))
	assert envelope["cache_schema"] == 1
	assert envelope["collection"]["raw_search_response"]["data"][0]["id"] == "listing-2"


def test_level2_cache_expires_after_local_day(cache_dir):
	client = CacheClient()
	local_zone = timezone(timedelta(hours=-6))

	cached_level2_collection(
		client,
		query(),
		cache_dir=cache_dir,
		clock=lambda: datetime(2026, 7, 21, 23, 59, tzinfo=local_zone),
	)
	result = cached_level2_collection(
		client,
		query(),
		cache_dir=cache_dir,
		clock=lambda: datetime(2026, 7, 22, 0, 1, tzinfo=local_zone),
	)

	assert result.cache_used is False
	assert client.search_calls == 2


def test_corrupt_level2_cache_is_replaced(cache_dir):
	client = CacheClient()
	first = cached_level2_collection(client, query(), cache_dir=cache_dir)
	first.cache_path.write_text("not json", encoding="utf-8")

	result = cached_level2_collection(client, query(), cache_dir=cache_dir)

	assert result.cache_used is False
	assert client.search_calls == 2
	assert json.loads(result.cache_path.read_text(encoding="utf-8"))["cache_schema"] == 1
