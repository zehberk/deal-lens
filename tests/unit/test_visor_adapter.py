import json

from pathlib import Path

from analysis.normalization import normalize_listing
from visor_api import adapt_facets_response, adapt_listing, adapt_search_response


FIXTURES = Path(__file__).parents[2] / "docs" / "fixtures" / "visor_api"


def fixture(name):
	with (FIXTURES / name).open(encoding="utf-8") as stream:
		return json.load(stream)


def test_adapter_maps_complete_search_and_detail_contract():
	search = fixture("listing_search.json")["data"][0]
	detail = fixture("listing_detail.json")["data"]

	listing = adapt_listing(search, detail)

	assert listing["id"] == "00000000000000000000000000000001"
	assert listing["vin"] == "1TEST000000000001"
	assert listing["title"] == "2026 Example Make Example Model Example Trim"
	assert listing["year"] == 2026
	assert listing["trim"] == "Example Trim"
	assert listing["msrp"] == 35000
	assert listing["price"] == 34000
	assert listing["mileage"] == 0
	assert listing["days_on_market"] == 3
	assert listing["condition"] == "New"
	assert listing["listed"] == "2026-01-02"
	assert listing["listing_url"].startswith("https://dealer.example.invalid/")
	assert listing["images"] == ["https://images.example.invalid/vehicle-1.jpg"]
	assert listing["seller"] == {
		"name": "Example Motors",
		"location": "Example City, CO 00000",
		"phone": "(000) 000-0000",
		"stock_number": None,
	}
	assert listing["specs"] == {
		"Trim Version": "Example Trim Hybrid",
		"Body Style": "Sedan",
		"Drivetrain": "FWD",
		"Fuel Type": "Hybrid",
		"Powertrain Type": "HEV",
		"Transmission": "CVT",
		"Engine": "2.5L I4",
		"Cylinders": 4,
		"Doors": 4,
		"Seating Capacity": 5,
		"Exterior Color": "Example Blue",
		"Interior Color": None,
		"Base Exterior Color": "Blue",
		"Base Interior Color": None,
		"Assembly Location": None,
	}
	assert listing["installed_addons"] == {
		"items": [{"name": "Example Package", "price": 500, "code": "PKG1"}],
		"total": 500,
	}
	assert listing["price_history"] == []
	assert listing["additional_docs"] == {
		"carfax_url": None,
		"autocheck_url": None,
		"window_sticker_url": None,
	}
	assert listing["source_data"]["detail_listing"]["pricing"] == detail["pricing"]
	assert listing["source_data"]["search_listing"]["features"] == search["features"]
	assert listing["provenance"]["title"]["kind"] == "calculated"
	assert listing["provenance"]["msrp"]["api_path"] == "vehicle.build.combined_msrp"
	assert listing["provenance"]["days_on_market"]["api_path"] == "days_on_market"
	assert listing["provenance"]["additional_docs.carfax_url"]["reason"] == "not_provided_by_api"
	assert listing["warnings"] == []


def test_detail_values_are_preferred_but_nulls_fall_back_to_search():
	search = {"id": 123, "price": 20_000, "miles": 12, "year": 2024, "make": "A", "model": "B"}
	detail = {"id": 123, "price": 19_000, "miles": None, "vehicle": {"build": {"year": 2025}}}

	listing = adapt_listing(search, detail)

	assert listing["id"] == "123"
	assert listing["price"] == 19_000
	assert listing["mileage"] == 12
	assert listing["year"] == 2025


def test_unknown_condition_and_unrequested_collections_remain_unavailable():
	listing = adapt_listing({"id": "one", "inventory_type": "fleet"})

	assert listing["condition"] is None
	assert listing["installed_addons"] == {"items": None, "total": None}
	assert listing["price_history"] is None
	assert listing["provenance"]["condition"]["reason"] == "unknown_inventory_type"


def test_price_history_has_explicit_compatibility_shape_without_fabricated_fields():
	listing = adapt_listing(
		{"id": "one"},
		{"price_history": [{"timestamp": "2026-01-01T00:00:00Z", "price": 10_000, "change": -500, "source": "dealer"}]},
	)

	assert listing["price_history"] == [{"date": "2026-01-01T00:00:00Z", "price": 10_000, "price_change": -500}]
	assert "lowest" not in listing["price_history"][0]
	assert "mileage" not in listing["price_history"][0]
	assert listing["source_data"]["detail_listing"]["price_history"][0]["source"] == "dealer"


def test_documented_price_history_maps_before_after_mileage_and_change():
	listing = adapt_listing(
		{"id": "one"},
		{
			"price_history": [{
				"changed_at": "2026-01-02T00:00:00Z",
				"miles": 25_000,
				"price_before": 21_000,
				"price_after": 20_000,
			}]
		},
	)

	assert listing["price_history"] == [{
		"date": "2026-01-02T00:00:00Z",
		"price": 20_000,
		"price_change": -1_000,
		"mileage": 25_000,
		"price_before": 21_000,
		"price_after": 20_000,
	}]


def test_search_msrp_is_used_when_detail_build_msrp_is_unavailable():
	listing = adapt_listing({
		"id": "one",
		"msrp": 30_000,
		"price": 28_000,
		"miles": 40_000,
		"days_on_market": 12,
	})

	assert listing["msrp"] == 30_000
	assert listing["price"] == 28_000
	assert listing["mileage"] == 40_000
	assert listing["days_on_market"] == 12


def test_missing_values_are_recorded_without_treating_zero_as_missing():
	listing = adapt_listing({"id": "one", "miles": 0})

	warnings_by_field = {warning["field"]: warning for warning in listing["warnings"]}
	assert warnings_by_field["vin"]["code"] == "missing_data"
	assert warnings_by_field["price"]["code"] == "missing_data"
	assert warnings_by_field["images"]["code"] == "missing_data"
	assert warnings_by_field["installed_addons.items"]["code"] == "missing_data"
	assert warnings_by_field["price_history"]["code"] == "missing_data"
	assert "mileage" not in warnings_by_field


def test_incompatible_values_are_recorded_without_copying_values_to_warnings():
	listing = adapt_listing({
		"id": "one",
		"price": "unknown",
		"miles": {"value": 10_000},
		"photo_urls": "https://example.invalid/photo.jpg",
		"options": {"name": "Package"},
		"price_history": "unknown",
	})

	warnings_by_field = {warning["field"]: warning for warning in listing["warnings"]}
	assert warnings_by_field["price"] == {
		"code": "incompatible_data",
		"field": "price",
		"message": "price must be a number.",
		"source": "visor_api",
		"api_path": "price",
		"received_type": "str",
	}
	assert warnings_by_field["mileage"]["received_type"] == "dict"
	assert warnings_by_field["images"]["received_type"] == "str"
	assert warnings_by_field["installed_addons.items"]["received_type"] == "dict"
	assert warnings_by_field["price_history"]["received_type"] == "str"
	assert "unknown" not in str(listing["warnings"])


def test_listing_warnings_are_aggregated_in_response_metadata():
	result = adapt_search_response({
		"data": [{"id": "one", "vin": None, "miles": 0}],
		"pagination": {},
		"meta": {},
	})

	vin_warning = next(
		warning for warning in result["metadata"]["warnings"]
		if warning["field"] == "vin"
	)
	assert vin_warning["listing_id"] == "one"
	assert vin_warning["vin"] is None


def test_search_envelope_and_facets_map_to_metadata_without_touching_listings():
	search_response = fixture("listing_search.json")
	facets_response = fixture("facets.json")

	result = adapt_search_response(
		search_response,
		request_filters={"make": "Example Make"},
		facets_response=facets_response,
		captured_at="2026-07-18T12:00:00+00:00",
	)

	assert set(result) == {"metadata", "listings", "source_data", "facet_result"}
	assert result["metadata"]["vehicle"] == {
		"make": "Example Make",
		"model": "Example Model",
		"trim": "Example Trim",
		"year": 2026,
	}
	assert result["metadata"]["site_info"]["total_for_sale"] == 100
	assert result["metadata"]["site_info"]["stats"]["price"]["missing"] == 10
	assert "stats" not in result["listings"][0]
	assert result["metadata"]["pagination"] == search_response["pagination"]
	assert result["facet_result"]["request_filters"] == {"make": "Example Make"}
	assert result["facet_result"]["captured_at"] == "2026-07-18T12:00:00+00:00"
	assert result["metadata"]["site_info"]["days_on_market"]["overall"] == (
		facets_response["data"]["stats"]["days_on_market"]
	)


def test_trim_facet_stats_are_kept_separate_with_exact_queries():
	search_response = fixture("listing_search.json")
	overall = fixture("facets.json")
	lx = fixture("facets.json")
	lx["data"]["total"] = 40
	lx["data"]["stats"]["days_on_market"]["count"] = 40
	lx["data"]["stats"]["days_on_market"]["mean"] = 21.5

	result = adapt_search_response(
		search_response,
		request_filters={"make": "Example Make", "trim": ["LX", "Sport"]},
		facets_response=overall,
		trim_facets_responses={"LX": lx},
		captured_at="2026-07-18T12:00:00+00:00",
	)

	assert result["metadata"]["site_info"]["days_on_market"]["by_trim"]["LX"]["mean"] == 21.5
	assert result["trim_facet_results"]["LX"]["total"] == 40
	assert result["trim_facet_results"]["LX"]["request_filters"] == {
		"make": "Example Make",
		"trim": ["LX"],
	}


def test_source_metadata_is_copied_to_saved_metadata():
	source_metadata = {
		"listings": {
			"endpoint": "/v1/listings",
			"query": {"make": ["Example Make"]},
			"max_listings": 10,
			"retrieved_at": "2026-07-20T12:00:00+00:00",
		},
		"facets": {
			"overall": {
				"endpoint": "/v1/facets",
				"query": {"make": ["Example Make"], "facets": "model"},
				"retrieved_at": "2026-07-20T12:00:01+00:00",
			},
			"by_trim": {},
		},
	}

	result = adapt_search_response(
		fixture("listing_search.json"),
		source_metadata=source_metadata,
	)

	assert result["metadata"]["sources"]["visor_api"] == source_metadata
	source_metadata["listings"]["query"]["make"].append("Changed")
	assert result["metadata"]["sources"]["visor_api"]["listings"]["query"] == {
		"make": ["Example Make"],
	}


def test_facet_adapter_preserves_null_or_missing_sections():
	result = adapt_facets_response({"data": {"total": 0}}, captured_at="now")

	assert result["total"] == 0
	assert result["facets"] is None
	assert result["range_facets"] is None
	assert result["stats"] is None


def test_api_build_facts_drive_existing_normalized_fields():
	search = fixture("listing_search.json")["data"][0]
	detail = fixture("listing_detail.json")["data"]

	normalized = normalize_listing(adapt_listing(search, detail))

	assert normalized["trim_version"] == "Example Trim Hybrid"
	assert normalized["is_hybrid"] is True
	assert normalized["is_plugin"] is False
	assert normalized["window_sticker_present"] is False
	assert normalized["report_present"] is None
	assert normalized["warranty_info_present"] is None
