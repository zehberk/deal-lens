from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from deal_lens.cli import (
	apply_url_to_args,
	collect_and_run_level1_api,
	collect_and_run_level2_api,
	collect_and_run_level3_api,
	configure_logging,
	format_runtime,
	run_with_runtime,
	scrape,
)


def test_cli_ignores_unsupported_url_options_and_keeps_supported_filters(
	monkeypatch,
):
	output = []
	monkeypatch.setattr("deal_lens.cli.CLI_CONSOLE.print", output.append)
	args = Namespace(
		url=(
			"https://visor.vin/search/filters?make=Toyota&model=4Runner"
			"&agnostic=false&zipcode=80203&year=2026%2C2025"
		)
	)

	result = apply_url_to_args(args)

	assert result.make == "Toyota"
	assert result.model == "4Runner"
	assert result.year == ["2025", "2026"]
	assert result._visor_query.filters == {
		"make": ("Toyota",),
		"model": ("4Runner",),
		"year": ("2025", "2026"),
	}
	assert result._visor_query.unsupported == {}
	assert output == [
		"[yellow]Ignoring unsupported URL options:[/] agnostic, zipcode"
	]


def test_logging_records_exact_command_in_timestamped_file(monkeypatch):
	file_handler = MagicMock()
	file_handler_type = MagicMock(return_value=file_handler)
	basic_config = MagicMock()
	logger = MagicMock()
	log_dir = MagicMock()
	log_path = Path("logs/deal-lens-20260723-212459-123456.log")
	log_dir.__truediv__.return_value = log_path
	clock = MagicMock()
	clock.now.return_value.strftime.return_value = "20260723-212459-123456"
	monkeypatch.setattr("deal_lens.cli.logging.FileHandler", file_handler_type)
	monkeypatch.setattr("deal_lens.cli.logging.basicConfig", basic_config)
	monkeypatch.setattr("deal_lens.cli.logging.getLogger", lambda _name=None: logger)
	monkeypatch.setattr("deal_lens.cli.datetime", clock)

	result = configure_logging(
		["--url", "https://visor.test/search?make=Hyundai IONIQ", "--level1"],
		log_dir=log_dir,
	)

	assert result == log_path
	log_dir.mkdir.assert_called_once_with(parents=True, exist_ok=True)
	file_handler_type.assert_called_once_with(log_path, encoding="utf-8")
	assert basic_config.call_args.kwargs["handlers"] == [file_handler]
	logger.debug.assert_called_once_with(
		"Command: %s",
		'deal-lens --url "https://visor.test/search?make=Hyundai IONIQ" --level1',
	)


async def test_level1_cli_routes_to_facet_api(monkeypatch):
	calls = []
	announcements = []

	async def fake_collect(args):
		calls.append(args)

	monkeypatch.setattr(
		"deal_lens.cli.collect_and_run_level1_api", fake_collect
	)
	monkeypatch.setattr(
		"deal_lens.cli.CLI_CONSOLE.print", announcements.append
	)
	args = Namespace(level1=True, level2=False, level3=False)

	await scrape(args)

	assert calls == [args]
	assert announcements == ["[bold cyan]Running Level 1 analysis[/]"]


async def test_level3_cli_routes_to_listing_api(monkeypatch):
	calls = []

	async def fake_collect(args):
		calls.append(args)

	monkeypatch.setattr(
		"deal_lens.cli.collect_and_run_level3_api", fake_collect
	)
	args = Namespace(level1=False, level2=False, level3=True)

	await scrape(args)

	assert calls == [args]


async def test_scrape_requires_an_analysis_level():
	args = Namespace(level1=False, level2=False, level3=False)

	with pytest.raises(ValueError, match="analysis level is required"):
		await scrape(args)


def test_runtime_format_is_compact_and_readable():
	assert format_runtime(8.562) == "8.6s"
	assert format_runtime(83.25) == "1m 23.2s"
	assert format_runtime(3_723.25) == "1h 2m 3.2s"


async def test_cli_reports_total_runtime(monkeypatch):
	args = Namespace(level1=False, level2=True, level3=False)
	clock = iter((100.0, 183.25)).__next__
	output = []

	async def fake_scrape(actual_args):
		assert actual_args is args

	monkeypatch.setattr("deal_lens.cli.scrape", fake_scrape)
	monkeypatch.setattr("deal_lens.cli.CLI_CONSOLE.print", output.append)

	await run_with_runtime(args, clock=clock)

	assert output == ["[bold green]Total runtime:[/] 1m 23.2s"]


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
	progress = object()
	pricing_cache = {"entries": {}}
	monkeypatch.setattr(
		"deal_lens.cli.VisorListingQuery.from_url", lambda url: query
	)
	monkeypatch.setattr("deal_lens.cli.get_visor_api_key", lambda: "key")
	monkeypatch.setattr("deal_lens.cli.VisorClient", lambda key, **kwargs: client)
	monkeypatch.setattr("deal_lens.cli.cli_progress", lambda: progress)
	monkeypatch.setattr("deal_lens.cli.cached_level1_facets", fake_cached)
	monkeypatch.setattr("deal_lens.cli.load_cache", lambda path: pricing_cache)
	monkeypatch.setattr("deal_lens.cli.get_level1_kbb_valuations", fake_kbb)
	monkeypatch.setattr("deal_lens.cli.build_market_snapshot", fake_snapshot)
	monkeypatch.setattr("deal_lens.cli.render_level1_market_pdf", fake_render)

	await collect_and_run_level1_api(Namespace(url="search-url", force=True))

	assert calls["cached"] == (
		client,
		query,
		{"cache_dir": Path("cache/level1"), "force": True, "progress": progress},
	)
	assert calls["kbb"] == (
		"Honda",
		"Civic",
		collection,
		pricing_cache,
		{"progress": progress},
	)
	assert calls["snapshot"] == (query, collection, kbb)
	assert calls["render"] == (snapshot, kbb)


async def test_level2_delegates_document_collection_to_analysis(monkeypatch):
	query = object()
	client = object()
	progress = object()
	listing = {"id": "listing-1", "vin": "TESTVIN"}
	collection = SimpleNamespace(
		listings=(SimpleNamespace(listing=listing),),
	)
	metadata = {"site_info": {}, "runtime": {}, "warnings": []}
	order = []

	monkeypatch.setattr(
		"deal_lens.cli.VisorListingQuery.from_url", lambda url: query
	)
	monkeypatch.setattr("deal_lens.cli._visor_client", lambda actual: client)
	monkeypatch.setattr("deal_lens.cli.cli_progress", lambda: progress)
	monkeypatch.setattr(
		"deal_lens.cli.cached_level2_collection",
		lambda *args, **kwargs: SimpleNamespace(
			collection=collection, cache_used=False
		),
	)
	monkeypatch.setattr("deal_lens.cli.build_metadata", lambda args: metadata)
	monkeypatch.setattr(
		"deal_lens.cli.apply_level2_collection_metadata",
		lambda *args: None,
	)
	monkeypatch.setattr(
		"deal_lens.cli.save_results",
		lambda *args: "20260728_120000",
	)

	async def fake_analysis(actual_metadata, listings, filename):
		order.append(("analysis", listings, filename))

	monkeypatch.setattr("deal_lens.cli.start_level2_analysis", fake_analysis)
	args = Namespace(
		url="search-url",
		make="INFINITI",
		model="QX55",
		max_listings=25,
		force=False,
		save_docs=True,
	)

	await collect_and_run_level2_api(args)

	filename = "output/raw/INFINITI_QX55_listings_20260728_120000.json"
	assert order == [("analysis", [listing], filename)]


async def test_level3_api_workflow_forwards_collection_options(monkeypatch):
	query = object()
	client = object()
	progress = object()
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
		"deal_lens.cli.VisorListingQuery.from_url", lambda url: query
	)
	monkeypatch.setattr("deal_lens.cli.get_visor_api_key", lambda: "key")
	monkeypatch.setattr("deal_lens.cli.VisorClient", lambda key, **kwargs: client)
	monkeypatch.setattr("deal_lens.cli.cli_progress", lambda: progress)
	monkeypatch.setattr("deal_lens.cli.cached_listing_search", fake_cached)
	monkeypatch.setattr("deal_lens.cli.save_results", fake_save)
	monkeypatch.setattr("deal_lens.cli.run_analysis", fake_analysis)
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
			"progress": progress,
		},
	)
	assert calls["save"] == (listings, metadata, args)
	assert calls["analysis"][-2:] == (
		"20260722_120000",
		"output/raw/Honda_Civic_listings_20260722_120000.json",
	)
