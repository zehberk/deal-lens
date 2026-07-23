from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from visor_scraper.scraper import (
	collect_and_run_level1_api,
	collect_and_run_level3_api,
	scrape,
)


async def test_level1_scrape_routes_to_facet_api(monkeypatch):
	calls = []

	async def fake_collect(args):
		calls.append(args)

	monkeypatch.setattr(
		"visor_scraper.scraper.collect_and_run_level1_api", fake_collect
	)
	args = Namespace(level1=True, level2=False, level3=False)

	await scrape(args)

	assert calls == [args]


async def test_level3_scrape_routes_to_listing_api(monkeypatch):
	calls = []

	async def fake_collect(args):
		calls.append(args)

	monkeypatch.setattr(
		"visor_scraper.scraper.collect_and_run_level3_api", fake_collect
	)
	args = Namespace(level1=False, level2=False, level3=True)

	await scrape(args)

	assert calls == [args]


async def test_scrape_requires_an_analysis_level():
	args = Namespace(level1=False, level2=False, level3=False)

	with pytest.raises(ValueError, match="analysis level is required"):
		await scrape(args)


async def test_level1_api_workflow_forwards_force_and_renders(monkeypatch):
	collection = object()
	kbb = object()
	snapshot = object()
	calls = {}

	class FakeQuery:
		def market_filters(self):
			return {
				"make": ("Honda",),
				"model": ("Civic",),
				"postal_code": "80202",
			}

	def fake_cached(client, query, **kwargs):
		calls["cached"] = (client, query, kwargs)
		return SimpleNamespace(collection=collection)

	async def fake_kbb(make, model, facets, cache, **kwargs):
		calls["kbb"] = (make, model, facets, cache, kwargs)
		return kbb

	def fake_snapshot(query, facets, valuations):
		calls["snapshot"] = (query, facets, valuations)
		return snapshot

	async def fake_render(market_snapshot, valuations):
		calls["render"] = (market_snapshot, valuations)
		return Path("output/level1/report.pdf")

	query = FakeQuery()
	client = object()
	pricing_cache = {"entries": {}}
	monkeypatch.setattr(
		"visor_scraper.scraper.VisorListingQuery.from_url", lambda url: query
	)
	monkeypatch.setattr("visor_scraper.scraper.get_visor_api_key", lambda: "key")
	monkeypatch.setattr("visor_scraper.scraper.VisorClient", lambda key: client)
	monkeypatch.setattr("visor_scraper.scraper.cached_level1_facets", fake_cached)
	monkeypatch.setattr("visor_scraper.scraper.load_cache", lambda path: pricing_cache)
	monkeypatch.setattr("visor_scraper.scraper.get_level1_kbb_valuations", fake_kbb)
	monkeypatch.setattr("visor_scraper.scraper.build_market_snapshot", fake_snapshot)
	monkeypatch.setattr("visor_scraper.scraper.render_level1_market_pdf", fake_render)

	await collect_and_run_level1_api(Namespace(url="search-url", force=True))

	assert calls["cached"] == (
		client,
		query,
		{"cache_dir": Path("cache/level1"), "force": True},
	)
	assert calls["kbb"] == (
		"Honda",
		"Civic",
		collection,
		pricing_cache,
		{"postal_code": "80202"},
	)
	assert calls["snapshot"] == (query, collection, kbb)
	assert calls["render"] == (snapshot, kbb)


async def test_level3_api_workflow_forwards_collection_options(monkeypatch):
	query = object()
	client = object()
	listings = [{"id": "listing-1", "vin": "TESTVIN"}]
	metadata = {"sources": {"visor_api": {}}}
	calls = {}

	def fake_cached(api_client, api_query, **kwargs):
		calls["cached"] = (api_client, api_query, kwargs)
		return SimpleNamespace(payload={"listings": listings, "metadata": metadata})

	def fake_save(saved_listings, saved_metadata, args):
		calls["save"] = (saved_listings, saved_metadata, args)
		return "20260722_120000"

	async def fake_analysis(saved_listings, saved_metadata, args, timestamp, filename):
		calls["analysis"] = (
			saved_listings,
			saved_metadata,
			args,
			timestamp,
			filename,
		)

	monkeypatch.setattr(
		"visor_scraper.scraper.VisorListingQuery.from_url", lambda url: query
	)
	monkeypatch.setattr("visor_scraper.scraper.get_visor_api_key", lambda: "key")
	monkeypatch.setattr("visor_scraper.scraper.VisorClient", lambda key: client)
	monkeypatch.setattr("visor_scraper.scraper.cached_listing_search", fake_cached)
	monkeypatch.setattr("visor_scraper.scraper.save_results", fake_save)
	monkeypatch.setattr("visor_scraper.scraper.run_analysis", fake_analysis)
	args = Namespace(
		url="search-url",
		make="Honda",
		model="Civic",
		max_listings=25,
		force=True,
		save_docs=False,
		level1=False,
		level2=False,
		level3=True,
	)

	await collect_and_run_level3_api(args)

	assert calls["cached"] == (
		client,
		query,
		{
			"cache_dir": Path("cache/level3"),
			"max_listings": 25,
			"force": True,
			"include_projection": True,
		},
	)
	assert calls["save"] == (listings, metadata, args)
	assert calls["analysis"][-2:] == (
		"20260722_120000",
		"output/raw/Honda_Civic_listings_20260722_120000.json",
	)
