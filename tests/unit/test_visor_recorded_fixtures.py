import json
import shutil
import uuid

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from analysis.level1_kbb import Level1KBBResult
from analysis.level1_market import build_market_snapshot
from visor_api import (
	VisorClient,
	VisorListingQuery,
	cached_level1_facets,
	collect_level2_listings,
)


FIXTURES = (
	Path(__file__).parents[2]
	/ "docs"
	/ "fixtures"
	/ "visor_api"
	/ "ioniq5_2024_2026"
)
QUERY = VisorListingQuery.from_options({
	"make": "Hyundai",
	"model": "IONIQ 5",
	"year": (2024, 2025, 2026),
	"price_max": 55_000,
	"sort": "newest",
})


def load_fixture(name):
	with (FIXTURES / name).open(encoding="utf-8-sig") as stream:
		return json.load(stream)


class RecordedResponse:
	def __init__(self, body):
		self.status = 200
		self.data = json.dumps(body).encode()
		self.headers = {}


class RecordedListingTransport:
	def __init__(self, *, fit_requested_limit=False):
		self.pages = [
			load_fixture("listing_search_page_1.json"),
			load_fixture("listing_search_page_2.json"),
		]
		self.requests = []
		self.fit_requested_limit = fit_requested_limit

	def __call__(self, method, url, **kwargs):
		self.requests.append((method, url, kwargs))
		page = self.pages.pop(0)
		if self.fit_requested_limit:
			query = parse_qs(urlparse(url).query)
			limit = int(query["limit"][0])
			offset = int(query["offset"][0])
			page = {
				**page,
				"data": page["data"][:limit],
				"pagination": {
					**page["pagination"],
					"limit": limit,
					"offset": offset,
					"next_offset": offset + limit,
				},
			}
		return RecordedResponse(page)


class RecordedLevel2Client:
	def __init__(self, listing_client):
		self.listing_client = listing_client

	def filter_all_listings(self, params=None, *, max_listings=50):
		return self.listing_client.filter_all_listings(
			params, max_listings=max_listings
		)

	def get_listing(self, listing_id, params=None):
		return {"data": {"id": listing_id}}


class NoFacetRequests:
	def filter_facets_model_with_headers(self, params=None):
		raise AssertionError("recorded Level 1 facets should be loaded from cache")


@pytest.fixture
def cache_dir():
	path = Path("cache") / "test-recorded-fixtures" / uuid.uuid4().hex
	path.mkdir(parents=True)
	try:
		yield path
	finally:
		shutil.rmtree(path)


def test_recorded_pages_replay_cost_conscious_pagination():
	transport = RecordedListingTransport()
	client = VisorClient("fixture-key", opener=transport)

	response = client.filter_all_listings(
		QUERY.api_params(include_projection=False), max_listings=150
	)

	assert len(response["data"]) == 150
	assert [
		parse_qs(urlparse(request[1]).query)["limit"]
		for request in transport.requests
	] == [["100"], ["50"]]
	assert [
		parse_qs(urlparse(request[1]).query)["offset"]
		for request in transport.requests
	] == [["0"], ["100"]]
	assert all(
		request[2]["headers"]["Authorization"] == "Bearer fixture-key"
		for request in transport.requests
	)


def test_recorded_pages_request_only_the_22_remaining_listings():
	transport = RecordedListingTransport(fit_requested_limit=True)
	client = VisorClient("fixture-key", opener=transport)

	response = client.filter_all_listings(max_listings=122)

	assert len(response["data"]) == 122
	assert [
		parse_qs(urlparse(request[1]).query)["limit"][0]
		for request in transport.requests
	] == ["100", "22"]


def test_recorded_pages_flow_through_level2_collection_and_adapter():
	transport = RecordedListingTransport()
	listing_client = VisorClient("fixture-key", opener=transport)

	collection = collect_level2_listings(
		RecordedLevel2Client(listing_client),
		QUERY,
		max_listings=150,
		clock=lambda: datetime(2026, 7, 23, 18, tzinfo=timezone.utc),
	)

	assert len(collection.listings) == 150
	assert collection.exclusions == ()
	assert collection.listings[0].listing["title"].startswith("2026 Hyundai IONIQ 5")
	assert collection.listings[0].listing["price"] <= 55_000
	assert collection.listings[-1].search_record["days_on_market"] >= (
		collection.listings[0].search_record["days_on_market"]
	)
	assert len(transport.requests) == 2


def test_recorded_facets_flow_through_level1_cache_and_market_snapshot(cache_dir):
	envelope = load_fixture("level1_facets.json")
	cache_path = cache_dir / "visor-level1-45c396e222f13acc.json"
	cache_path.write_text(json.dumps(envelope), encoding="utf-8")

	result = cached_level1_facets(
		NoFacetRequests(),
		QUERY,
		cache_dir=cache_dir,
		clock=lambda: datetime(2026, 7, 23, 18, tzinfo=timezone.utc),
	)
	snapshot = build_market_snapshot(
		QUERY,
		result.collection,
		Level1KBBResult(matches=(), failures=()),
		generated_at="2026-07-23T16:00:00+00:00",
	)

	assert result.cache_used is True
	assert len(result.collection.responses) == 22
	assert snapshot.scope.make == "Hyundai"
	assert snapshot.scope.model == "IONIQ 5"
	assert snapshot.scope.years == (2024, 2025, 2026)
	assert snapshot.active.inventory_count > 0
	assert snapshot.year_trim_summaries


def test_recorded_fixtures_contain_no_credentials_or_response_headers():
	fixture_text = "\n".join(
		path.read_text(encoding="utf-8-sig") for path in FIXTURES.glob("*.json")
	).casefold()

	assert "authorization" not in fixture_text
	assert "bearer " not in fixture_text
	assert "api_key" not in fixture_text
	assert "usage_headers" not in fixture_text


def test_normal_suite_blocks_real_network_connections():
	client = VisorClient(
		"fixture-key",
		base_url="http://127.0.0.1:9",
		max_retries=0,
	)

	with pytest.raises(AssertionError, match="must use recorded fixtures"):
		client.filter_listings()
