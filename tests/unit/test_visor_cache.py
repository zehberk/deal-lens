import json
import shutil
import uuid

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from visor_api import VisorListingQuery, cached_listing_search


QUERY = VisorListingQuery.from_options({
	"make": "Honda",
	"model": "Civic",
	"trim": ("LX", "Sport"),
	"year": 2020,
	"miles_min": 10_000,
	"miles_max": 80_000,
	"sort": "lowest price",
})


class FakeClient:
	def __init__(self) -> None:
		self.calls: list[tuple[dict[str, str | tuple[str, ...]], int]] = []

	def filter_all_listings(
		self,
		params: dict[str, str | tuple[str, ...]],
		*,
		max_listings: int,
	) -> dict[str, Any]:
		self.calls.append((params, max_listings))
		return {"data": [{"id": f"call-{len(self.calls)}"}], "pagination": {}}


@pytest.fixture
def cache_dir() -> Iterator[Path]:
	path = Path("cache") / "test-visor-cache" / uuid.uuid4().hex
	path.mkdir(parents=True)
	try:
		yield path
	finally:
		shutil.rmtree(path)


def test_first_search_calls_api_and_saves_metadata_cache(cache_dir):
	client = FakeClient()

	result = cached_listing_search(client, QUERY, cache_dir=cache_dir)

	assert result.cache_used is False
	assert len(client.calls) == 1
	assert result.cache_path.is_file()
	assert result.metadata["query"]["make"] == ["Honda"]
	assert result.metadata["query"]["trim"] == ["LX", "Sport"]
	assert result.metadata["query"]["year"] == ["2020"]
	assert result.metadata["query"]["min_mileage"] == "10000"
	assert result.metadata["query"]["max_mileage"] == "80000"
	assert result.metadata["query"]["sort"] == "price"
	assert json.loads(result.cache_path.read_text(encoding="utf-8"))["response"] == result.response


def test_same_search_uses_cache_without_api_call(cache_dir):
	client = FakeClient()
	first = cached_listing_search(client, QUERY, cache_dir=cache_dir)

	second = cached_listing_search(client, QUERY, cache_dir=cache_dir)

	assert second.cache_used is True
	assert second.cache_path == first.cache_path
	assert second.response == first.response
	assert len(client.calls) == 1


def test_force_search_calls_api_and_overrides_cache(cache_dir):
	client = FakeClient()
	first = cached_listing_search(client, QUERY, cache_dir=cache_dir)

	forced = cached_listing_search(client, QUERY, cache_dir=cache_dir, force=True)

	assert forced.cache_used is False
	assert forced.cache_path == first.cache_path
	assert forced.response != first.response
	assert len(client.calls) == 2
	assert json.loads(forced.cache_path.read_text(encoding="utf-8"))["response"] == forced.response
