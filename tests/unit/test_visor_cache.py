import json
import shutil
import uuid

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from visor_api import (
	FacetResponse,
	ListingSearchResponse,
	VisorListingQuery,
	cached_listing_search,
)


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
		self.facet_calls: list[dict[str, str | tuple[str, ...]]] = []

	def filter_all_listings_model(
		self,
		params: dict[str, str | tuple[str, ...]],
		*,
		max_listings: int,
	) -> ListingSearchResponse:
		self.calls.append((params, max_listings))
		return ListingSearchResponse.from_dict({
			"data": [{"id": f"call-{len(self.calls)}", "vin": "TESTVIN"}],
			"pagination": {
				"limit": max_listings,
				"offset": 0,
				"total": 1,
				"next_offset": None,
			},
			"meta": {},
		})

	def filter_facets_model(
		self,
		params: dict[str, str | tuple[str, ...]],
	) -> FacetResponse:
		self.facet_calls.append(params)
		trims = params.get("trim", ())
		trim = trims[0] if isinstance(trims, tuple) and len(trims) == 1 else ""
		total, mean, median = {
			"LX": (406, 50.0, 25),
			"Sport": (427, 54.0, 27),
		}.get(trim, (833, 52.1, 26))
		return FacetResponse.from_dict({
			"data": {
				"total": total,
				"facets": {},
				"range_facets": {},
				"stats": {
					"days_on_market": {
						"min": 0,
						"max": 180,
						"count": total,
						"missing": 0,
						"mean": mean,
						"median": median,
						"stddev": 75.0,
					},
				},
			},
			"meta": {
				"facets": [],
				"metric": "count",
				"sort": "-count",
				"minimum_metric_count": 1,
			},
		})


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
	assert client.facet_calls == [{
		"make": ("Honda",),
		"model": ("Civic",),
		"trim": ("LX", "Sport"),
		"year": ("2020",),
		"min_mileage": "10000",
		"max_mileage": "80000",
		"facets": "model,trim,days_on_market",
	}, {
		"make": ("Honda",),
		"model": ("Civic",),
		"trim": ("LX",),
		"year": ("2020",),
		"min_mileage": "10000",
		"max_mileage": "80000",
		"facets": "days_on_market",
	}, {
		"make": ("Honda",),
		"model": ("Civic",),
		"trim": ("Sport",),
		"year": ("2020",),
		"min_mileage": "10000",
		"max_mileage": "80000",
		"facets": "days_on_market",
	}]
	assert result.payload["metadata"]["site_info"]["total_for_sale"] == 833
	assert result.payload["metadata"]["site_info"]["days_on_market"] == {
		"overall": {
			"min": 0, "max": 180, "count": 833, "missing": 0,
			"mean": 52.1, "median": 26, "stddev": 75.0,
		},
		"by_trim": {
			"LX": {
				"min": 0, "max": 180, "count": 406, "missing": 0,
				"mean": 50.0, "median": 25, "stddev": 75.0,
			},
			"Sport": {
				"min": 0, "max": 180, "count": 427, "missing": 0,
				"mean": 54.0, "median": 27, "stddev": 75.0,
			},
		},
	}
	sources = result.payload["metadata"]["sources"]["visor_api"]
	assert sources["listings"] == {
		"endpoint": "/v1/listings",
		"query": result.metadata["query"],
		"max_listings": 10,
		"retrieved_at": result.metadata["listing_retrieved_at"],
	}
	assert sources["facets"]["overall"] == {
		"endpoint": "/v1/facets",
		"query": result.metadata["facet_query"],
		"retrieved_at": result.metadata["facet_retrieved_at"],
	}
	assert sources["facets"]["by_trim"]["LX"] == {
		"endpoint": "/v1/facets",
		"query": result.metadata["trim_facet_queries"]["LX"],
		"retrieved_at": result.metadata["trim_facets_retrieved_at"]["LX"],
	}
	assert result.payload["facet_result"]["captured_at"] == (
		result.metadata["facet_retrieved_at"]
	)
	assert result.payload["trim_facet_results"]["LX"]["captured_at"] == (
		result.metadata["trim_facets_retrieved_at"]["LX"]
	)
	assert "Authorization" not in json.dumps(sources)
	assert "api_key" not in json.dumps(sources)
	assert result.cache_path.is_file()
	assert result.metadata["query"]["make"] == ["Honda"]
	assert result.metadata["query"]["trim"] == ["LX", "Sport"]
	assert result.metadata["query"]["year"] == ["2020"]
	assert result.metadata["query"]["min_mileage"] == "10000"
	assert result.metadata["query"]["max_mileage"] == "80000"
	assert result.metadata["query"]["sort"] == "price"
	assert "fields" not in result.metadata["query"]
	assert "include" not in result.metadata["query"]
	envelope = json.loads(result.cache_path.read_text(encoding="utf-8"))
	assert envelope["response"] == result.response.to_dict()
	assert envelope["facets_response"] == result.facets_response.to_dict()
	assert set(envelope["trim_facets_responses"]) == {"LX", "Sport"}


def test_same_search_uses_cache_without_api_call(cache_dir):
	client = FakeClient()
	first = cached_listing_search(client, QUERY, cache_dir=cache_dir)

	second = cached_listing_search(client, QUERY, cache_dir=cache_dir)

	assert second.cache_used is True
	assert second.cache_path == first.cache_path
	assert second.response == first.response
	assert len(client.calls) == 1
	assert len(client.facet_calls) == 3
	assert second.payload["metadata"]["site_info"]["total_for_sale"] == 833
	assert set(second.trim_facets_responses) == {"LX", "Sport"}
	assert second.payload["metadata"]["sources"] == first.payload["metadata"]["sources"]


def test_force_search_calls_api_and_overrides_cache(cache_dir):
	client = FakeClient()
	first = cached_listing_search(client, QUERY, cache_dir=cache_dir)

	forced = cached_listing_search(client, QUERY, cache_dir=cache_dir, force=True)

	assert forced.cache_used is False
	assert forced.cache_path == first.cache_path
	assert forced.response != first.response
	assert len(client.calls) == 2
	assert len(client.facet_calls) == 6
	assert json.loads(forced.cache_path.read_text(encoding="utf-8"))["response"] == forced.response.to_dict()


def test_search_without_selected_trims_uses_only_overall_facets(cache_dir):
	client = FakeClient()
	query = VisorListingQuery.from_options({
		"year": 2020,
		"make": "Honda",
		"model": "Civic",
	})

	result = cached_listing_search(client, query, cache_dir=cache_dir)

	assert client.facet_calls == [{
		"year": ("2020",),
		"make": ("Honda",),
		"model": ("Civic",),
		"facets": "model,trim,days_on_market",
	}]
	assert result.trim_facets_responses == {}
	assert result.payload["metadata"]["site_info"]["days_on_market"]["by_trim"] == {}


def test_listing_cache_expires_after_the_local_calendar_day(cache_dir):
	client = FakeClient()
	local_zone = timezone(timedelta(hours=-6))
	cached_listing_search(
		client,
		QUERY,
		cache_dir=cache_dir,
		clock=lambda: datetime(2026, 7, 20, 8, tzinfo=local_zone),
	)
	same_day = cached_listing_search(
		client,
		QUERY,
		cache_dir=cache_dir,
		clock=lambda: datetime(2026, 7, 20, 23, 59, tzinfo=local_zone),
	)
	next_day = cached_listing_search(
		client,
		QUERY,
		cache_dir=cache_dir,
		clock=lambda: datetime(2026, 7, 21, 0, 1, tzinfo=local_zone),
	)

	assert same_day.cache_used is True
	assert next_day.cache_used is False
	assert len(client.calls) == 2
	assert len(client.facet_calls) == 6


def test_listing_cache_key_covers_normalized_query_and_maximum(cache_dir):
	client = FakeClient()
	equivalent = VisorListingQuery.from_options({
		"trim": ("Sport", "LX"),
		"model": "Civic",
		"make": "Honda",
		"year": 2020,
		"max_mileage": 80_000,
		"min_mileage": 10_000,
		"sort": "price",
	})
	changed_query = VisorListingQuery.from_options({
		**QUERY.filters,
		"year": 2021,
	})

	baseline = cached_listing_search(
		client, QUERY, cache_dir=cache_dir, max_listings=10
	)
	same = cached_listing_search(
		client, equivalent, cache_dir=cache_dir, max_listings=10
	)
	different_query = cached_listing_search(
		client, changed_query, cache_dir=cache_dir, max_listings=10
	)
	different_maximum = cached_listing_search(
		client, QUERY, cache_dir=cache_dir, max_listings=11
	)

	assert same.cache_used is True
	assert same.cache_path == baseline.cache_path
	assert different_query.cache_path != baseline.cache_path
	assert different_maximum.cache_path != baseline.cache_path
	assert len(client.calls) == 3
