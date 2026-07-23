from argparse import Namespace
from pathlib import Path

from analysis.analysis_utils import check_missing_docs
from visor_api import Level2Collection, Level2Exclusion, Level2ListingRecord
from deal_lens.cli import apply_level2_collection_metadata, scrape


def collection():
	return Level2Collection(
		listings=(
			Level2ListingRecord(
				listing_id="listing-1",
				vin="TESTVIN1",
				listing={
					"id": "listing-1",
					"vin": "TESTVIN1",
					"warnings": [{
						"field": "warranty",
						"code": "missing_data",
						"message": "Warranty is unavailable.",
					}],
				},
				search_record={"id": "listing-1", "vin": "TESTVIN1"},
				detail_record={"id": "listing-1", "vin": "TESTVIN1"},
			),
			Level2ListingRecord(
				listing_id="listing-2",
				vin="TESTVIN2",
				listing={"id": "listing-2", "vin": "TESTVIN2", "warnings": []},
				search_record={"id": "listing-2", "vin": "TESTVIN2"},
				detail_record=None,
				detail_error="Visor API read timeout",
			),
		),
		exclusions=(
			Level2Exclusion(
				index=2,
				reason="missing_stable_listing_id",
				vin="TESTVIN3",
			),
		),
		request_params={
			"make": ["Subaru"],
			"model": ["Forester"],
			"include": "options,price_history",
		},
		retrieved_at="2026-07-21T00:00:00+00:00",
		raw_search_response={
			"data": [],
			"pagination": {
				"limit": 10,
				"offset": 0,
				"total": 125,
				"next_offset": 10,
			},
			"meta": {},
		},
	)


def test_collection_metadata_preserves_api_provenance_and_failures():
	metadata = {
		"site_info": {},
		"runtime": {},
		"warnings": [],
	}

	apply_level2_collection_metadata(metadata, collection(), cache_used=True)

	assert metadata["site_info"]["total_for_sale"] == 125
	assert metadata["runtime"]["source"] == "visor_api"
	assert metadata["sources"]["visor_api"]["listings"] == {
		"endpoint": "/v1/listings",
		"query": {
			"make": ["Subaru"],
			"model": ["Forester"],
			"include": "options,price_history",
		},
		"retrieved_at": "2026-07-21T00:00:00+00:00",
		"cache_used": True,
	}
	assert metadata["sources"]["visor_api"]["details"]["requested"] == 2
	assert metadata["sources"]["visor_api"]["details"]["retrieved"] == 1
	assert metadata["warnings"][0]["listing_id"] == "listing-1"
	assert metadata["warnings"][1]["code"] == "excluded_api_record"
	assert metadata["warnings"][1]["vin"] == "TESTVIN3"


async def test_level2_cli_routes_to_api_collection(monkeypatch):
	calls = []

	async def fake_collect(args):
		calls.append(args)

	monkeypatch.setattr(
		"deal_lens.cli.collect_and_run_level2_api", fake_collect
	)
	args = Namespace(level1=False, level2=True, level3=False)

	await scrape(args)

	assert calls == [args]


def test_missing_document_check_ignores_null_and_legacy_sentinels(monkeypatch):
	downloads = []
	monkeypatch.setattr(
		"analysis.analysis_utils.get_vehicle_dir",
		lambda listing: Path("missing-vehicle-directory"),
	)
	monkeypatch.setattr(
		"analysis.analysis_utils.download_report_pdfs",
		lambda listings: downloads.extend(listings),
	)
	listings = [
		{"vin": "NULL", "additional_docs": {"carfax_url": None}},
		{"vin": "LEGACY", "additional_docs": {"carfax_url": "Unavailable"}},
		{"vin": "URL", "additional_docs": {"carfax_url": "https://example.invalid/report"}},
	]

	check_missing_docs(listings)

	assert [listing["vin"] for listing in downloads] == ["URL"]
