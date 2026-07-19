import json

from pathlib import Path

from visor_api import (
	adapt_search_response,
	FacetResponse,
	ListingDetailResponse,
	ListingSearchResponse,
	Pricing,
	VisorClient,
)


FIXTURES = Path(__file__).parents[2] / "docs" / "fixtures" / "visor_api"


class FakeResponse:
	def __init__(self, body):
		self.status = 200
		self.data = json.dumps(body).encode()
		self.headers = {}


def fixture(name):
	with (FIXTURES / name).open(encoding="utf-8") as stream:
		return json.load(stream)


def test_listing_search_model_loads_from_api_response():
	payload = fixture("listing_search.json")
	client = VisorClient(
		"test-api-key",
		opener=lambda *args, **kwargs: FakeResponse(payload),
	)

	response = client.filter_listings_model()

	assert isinstance(response, ListingSearchResponse)
	assert response.data[0].vin == "1TEST000000000001"
	assert response.data[0].msrp == 34_500
	assert response.data[0].days_on_market == 3
	assert response.data[0].options is not None
	assert response.data[0].options[0].name == "Example Package"
	assert response.pagination.total == 1


def test_listing_search_model_loads_from_serialized_cache():
	original = ListingSearchResponse.from_dict(fixture("listing_search.json"))

	restored = ListingSearchResponse.from_dict(original.to_dict())

	assert restored == original
	assert restored.data[0].dealer_name == "Example Motors"


def test_listing_search_model_flows_into_legacy_adapter():
	response = ListingSearchResponse.from_dict(fixture("listing_search.json"))

	adapted = adapt_search_response(response)

	assert adapted["listings"][0]["id"] == response.data[0].id
	assert adapted["listings"][0]["source_data"]["search_listing"]["msrp"] == 34_500


def test_listing_detail_model_covers_nested_api_contract():
	response = ListingDetailResponse.from_dict(fixture("listing_detail.json"))

	assert response.data.dealer.phone == "(000) 000-0000"
	assert response.data.vehicle.build.combined_msrp == 35_000
	assert response.data.vehicle.build.options is not None
	assert response.data.vehicle.build.options[0].code == "PKG1"
	assert isinstance(response.data.pricing, Pricing)
	assert response.data.pricing.displayed_provider_current_price_usd == 34_000
	assert response.data.pricing.line_items == []


def test_facet_model_covers_counts_ranges_stats_and_metadata():
	response = FacetResponse.from_dict(fixture("facets.json"))

	assert response.data.total == 100
	assert response.data.facets["make"][0].value == "Example Make"
	assert response.data.range_facets["price"].buckets[0].count == 40
	assert response.data.stats["price"].missing == 10
	assert response.meta.metric == "count"


def test_unknown_future_fields_survive_model_round_trip():
	payload = fixture("listing_search.json")
	payload["future_envelope_field"] = {"version": 2}
	payload["data"][0]["future_listing_field"] = "kept"

	serialized = ListingSearchResponse.from_dict(payload).to_dict()

	assert serialized["future_envelope_field"] == {"version": 2}
	assert serialized["data"][0]["future_listing_field"] == "kept"
