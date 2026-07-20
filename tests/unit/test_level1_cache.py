import json
import shutil
import uuid

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from visor_api import FacetResponse, VisorListingQuery, cached_level1_facets
from visor_api.client import QueryParams, QueryValue


class FakeCachedFacetClient:
	def __init__(self, *, fail_on_call=None):
		self.calls: list[dict[str, QueryValue]] = []
		self.fail_on_call = fail_on_call

	def filter_facets_model_with_headers(self, params: QueryParams | None = None):
		if params is None:
			raise AssertionError("facet parameters are required")
		self.calls.append(dict(params or {}))
		if len(self.calls) == self.fail_on_call:
			raise RuntimeError("simulated refresh failure")
		metric = str(params["metric"])
		total = 4 if "sold_within_days" in params else 10
		facet_names = str(params["facets"]).split(",")
		is_trim_metric = facet_names == ["trim"]
		stats = {
			name: {
				"min": 1,
				"max": 100,
				"count": total - 1,
				"missing": 1,
				"mean": 20,
				"median": 15,
				"stddev": 5,
			}
			for name in facet_names
			if name in {"price", "miles", "days_on_market"}
		}
		return FacetResponse.from_dict({
			"data": {
				"total": total,
				"facets": (
					{"trim": [{
						"value": "LX",
						"count": total,
						"metric": {"name": metric, "value": len(self.calls)},
					}]}
					if is_trim_metric else {}
				),
				"range_facets": {},
				"stats": stats,
			},
			"meta": {
				"facets": facet_names,
				"metric": metric,
				"sort": "-count",
				"minimum_metric_count": 5,
			},
		}), {"X-Usage-Cost": "1"}


@pytest.fixture
def cache_dir() -> Iterator[Path]:
	path = Path("cache") / "test-level1-cache" / uuid.uuid4().hex
	path.mkdir(parents=True)
	try:
		yield path
	finally:
		shutil.rmtree(path)


def query(year="2024"):
	return VisorListingQuery.from_options({
		"make": "Honda",
		"model": "Civic",
		"year": year,
		"condition": "used",
		"location": "80202",
	})


def test_fresh_run_caches_each_complete_query(cache_dir):
	client = FakeCachedFacetClient()

	result = cached_level1_facets(
		client,
		query("2023,2024"),
		cache_dir=cache_dir,
		clock=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
	)

	assert result.cache_used is False
	assert len(client.calls) == 10
	assert result.cache_path.is_file()
	envelope = json.loads(result.cache_path.read_text(encoding="utf-8"))
	assert len(envelope["entries"]) == 10
	assert all(
		entry["usage_headers"] == {"X-Usage-Cost": "1"}
		for entry in envelope["entries"].values()
	)
	assert all(
		item.retrieved_at == "2026-07-20T00:00:00+00:00"
		for item in result.collection.responses
	)


def test_normal_rerun_uses_cache_without_api_calls(cache_dir):
	client = FakeCachedFacetClient()
	first = cached_level1_facets(client, query(), cache_dir=cache_dir)

	second = cached_level1_facets(client, query(), cache_dir=cache_dir)

	assert second.cache_used is True
	assert len(client.calls) == 5
	assert second.collection == first.collection


def test_forced_refresh_replaces_every_response(cache_dir):
	client = FakeCachedFacetClient()
	first = cached_level1_facets(client, query(), cache_dir=cache_dir)
	first_payload = first.cache_path.read_text(encoding="utf-8")

	forced = cached_level1_facets(client, query(), cache_dir=cache_dir, force=True)

	assert forced.cache_used is False
	assert len(client.calls) == 10
	assert forced.cache_path.read_text(encoding="utf-8") != first_payload
	values = []
	for item in forced.collection.responses[:3]:
		metric = item.response.data.facets["trim"][0].metric
		assert metric is not None
		values.append(metric.value)
	assert values == [6, 7, 8]
	assert forced.collection.years[0].trims[0].active_price_stats is not None


def test_failed_forced_refresh_preserves_complete_previous_cache(cache_dir):
	first_client = FakeCachedFacetClient()
	first = cached_level1_facets(first_client, query(), cache_dir=cache_dir)
	first_payload = first.cache_path.read_text(encoding="utf-8")
	failing_client = FakeCachedFacetClient(fail_on_call=2)

	with pytest.raises(RuntimeError, match="refresh failure"):
		cached_level1_facets(
			failing_client,
			query(),
			cache_dir=cache_dir,
			force=True,
		)

	assert first.cache_path.read_text(encoding="utf-8") == first_payload
	assert cached_level1_facets(
		FakeCachedFacetClient(), query(), cache_dir=cache_dir
	).cache_used is True


def test_complete_query_changes_use_a_different_cache(cache_dir):
	client = FakeCachedFacetClient()
	first = cached_level1_facets(client, query(), cache_dir=cache_dir)
	changed = VisorListingQuery.from_options({
		"make": "Honda",
		"model": "Civic",
		"year": "2024",
		"condition": "certified",
		"location": "80202",
	})

	second = cached_level1_facets(client, changed, cache_dir=cache_dir)

	assert first.cache_path != second.cache_path
	assert len(client.calls) == 10


def test_cache_expires_after_the_local_calendar_day(cache_dir):
	client = FakeCachedFacetClient()
	local_zone = timezone(timedelta(hours=-6))
	first = cached_level1_facets(
		client,
		query(),
		cache_dir=cache_dir,
		clock=lambda: datetime(2026, 7, 20, 8, tzinfo=local_zone),
	)

	same_day = cached_level1_facets(
		client,
		query(),
		cache_dir=cache_dir,
		clock=lambda: datetime(2026, 7, 20, 23, 59, tzinfo=local_zone),
	)
	next_day = cached_level1_facets(
		client,
		query(),
		cache_dir=cache_dir,
		clock=lambda: datetime(2026, 7, 21, 0, 1, tzinfo=local_zone),
	)

	assert first.cache_used is False
	assert same_day.cache_used is True
	assert next_day.cache_used is False
	assert len(client.calls) == 10
