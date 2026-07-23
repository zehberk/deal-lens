from datetime import datetime, timezone

import pytest

from visor_api import (
	LEVEL2_SEARCH_EXPANSIONS,
	LEVEL2_SEARCH_FIELDS,
	VisorListingQuery,
	collect_level2_listings,
)


class FakeLevel2Client:
	def __init__(self, rows, details=None, detail_errors=None):
		self.rows = rows
		self.details = details or {}
		self.detail_errors = detail_errors or {}
		self.search_calls = []
		self.detail_calls = []

	def filter_all_listings(self, params=None, *, max_listings=50):
		self.search_calls.append((dict(params or {}), max_listings))
		return {
			"data": self.rows,
			"pagination": {
				"limit": max_listings,
				"offset": 0,
				"total": len(self.rows) if isinstance(self.rows, list) else 0,
				"next_offset": None,
			},
			"meta": {},
		}

	def get_listing(self, listing_id, params=None):
		self.detail_calls.append((listing_id, params))
		if listing_id in self.detail_errors:
			raise self.detail_errors[listing_id]
		return {"data": self.details.get(listing_id, {"id": listing_id})}


def query(**overrides):
	options = {
		"make": "Subaru",
		"model": "Crosstrek",
		"year": (2024, 2025, 2026),
		"max_mileage": 75_000,
		"sort": "lowest price",
	}
	options.update(overrides)
	return VisorListingQuery.from_options(options)


def search_row(listing_id="listing-1", vin="TESTVIN1"):
	return {
		"id": listing_id,
		"vin": vin,
		"year": 2024,
		"make": "Subaru",
		"model": "Crosstrek",
		"trim": "Premium",
		"price": 25_000,
		"miles": 10_000,
		"inventory_type": "certified",
		"options": [{"code": "OP1", "name": "Package", "msrp": 500}],
		"price_history": [],
	}


def detail_row(listing_id="listing-1"):
	return {
		"id": listing_id,
		"vin": "TESTVIN1",
		"dealer": {
			"dealer_id": "dealer-1",
			"name": "Example Subaru",
			"city": "Denver",
			"state": "CO",
			"postal_code": "80202",
			"phone": "(000) 000-0000",
		},
		"vehicle": {"build": {"version": "Premium AWD"}},
	}


def test_collection_uses_enriched_search_and_sequential_standard_details():
	client = FakeLevel2Client(
		[search_row(), search_row("listing-2", "TESTVIN2")],
		{"listing-1": detail_row(), "listing-2": detail_row("listing-2")},
	)

	result = collect_level2_listings(
		client,
		query(),
		max_listings=100,
		clock=lambda: datetime(2026, 7, 21, tzinfo=timezone.utc),
	)

	params, maximum = client.search_calls[0]
	assert maximum == 100
	assert params["fields"] == LEVEL2_SEARCH_FIELDS
	assert params["include"] == LEVEL2_SEARCH_EXPANSIONS
	assert params["sort"] == "price"
	assert "inventory_type" not in params
	assert client.detail_calls == [("listing-1", None), ("listing-2", None)]
	assert [item.listing_id for item in result.listings] == ["listing-1", "listing-2"]
	assert result.listings[0].vin == "TESTVIN1"
	assert result.listings[0].listing["seller"]["phone"] == "(000) 000-0000"
	assert result.retrieved_at == "2026-07-21T00:00:00+00:00"
	assert result.raw_search_response["data"][0]["options"][0]["code"] == "OP1"


def test_collection_preserves_search_listing_when_detail_fails():
	client = FakeLevel2Client(
		[search_row()],
		detail_errors={"listing-1": RuntimeError("detail unavailable")},
	)

	result = collect_level2_listings(client, query())
	record = result.listings[0]

	assert record.detail_record is None
	assert record.detail_error == "RuntimeError"
	assert record.listing["price"] == 25_000
	assert record.listing["provenance"]["detail"]["reason"] == "source_error"
	assert record.listing["warnings"][-1]["field"] == "detail"
	assert result.exclusions == ()


def test_collection_records_malformed_missing_and_duplicate_rows():
	client = FakeLevel2Client([
		"not-an-object",
		{"vin": "NOID"},
		search_row(),
		search_row(),
	])

	result = collect_level2_listings(client, query())

	assert len(result.listings) == 1
	assert [item.reason for item in result.exclusions] == [
		"search_record_not_object",
		"missing_stable_listing_id",
		"duplicate_stable_listing_id",
	]
	assert client.detail_calls == [("listing-1", None)]


def test_collection_rejects_non_array_search_data():
	client = FakeLevel2Client([])
	client.rows = None

	with pytest.raises(ValueError, match="must be an array"):
		collect_level2_listings(client, query())


def test_collection_rejects_unsupported_and_naive_clock():
	client = FakeLevel2Client([])
	unsupported = query(unknown_filter="value")
	with pytest.raises(ValueError, match="unsupported query options"):
		collect_level2_listings(client, unsupported)

	with pytest.raises(ValueError, match="aware datetime"):
		collect_level2_listings(
			client,
			query(),
			clock=lambda: datetime(2026, 7, 21),
		)
