from decimal import Decimal

from deal_lens.models import (
	DataWarning,
	DealerFee,
	InstalledOption,
	ListingCondition,
	PriceHistoryRecord,
	SourceProvenance,
	WarrantyCoverage,
	listing_from_legacy,
)
from visor_api.adapter import listing_from_visor


def test_legacy_factory_preserves_source_facts_and_unknown_fields():
	source = {
		"id": "listing-1",
		"vin": "TESTVIN",
		"year": 2025,
		"trim": "Limited",
		"condition": "Used",
		"price": 25_500,
		"mileage": 12_345,
		"custom_legacy_field": "preserved",
		"source_data": {"provider": "legacy_scraper", "record": {"a": 1}},
	}

	listing = listing_from_legacy(source)

	assert listing.id == "listing-1"
	assert listing.vin == "TESTVIN"
	assert listing.condition is ListingCondition.USED
	assert listing.price == Decimal("25500")
	assert listing.mileage == 12_345
	assert listing.source == "legacy_scraper"
	assert listing.extra["custom_legacy_field"] == "preserved"
	assert listing.to_dict()["source_data"] == source["source_data"]


def test_missing_values_remain_unknown():
	listing = listing_from_legacy({"id": "listing-1"})

	assert listing.vin is None
	assert listing.year is None
	assert listing.price is None
	assert listing.mileage is None
	assert listing.condition is None


def test_visor_factory_prefers_detail_and_retains_provenance():
	listing = listing_from_visor(
		{
			"id": "listing-1",
			"vin": "TESTVIN",
			"year": 2024,
			"make": "Example",
			"model": "Car",
			"price": 30_000,
			"miles": 100,
		},
		{"id": "listing-1", "price": 29_000},
	)

	assert listing.price == Decimal("29000")
	assert listing.vehicle.make == "Example"
	assert listing.vehicle.model == "Car"
	assert listing.source == "visor_api"
	assert listing.raw_source["search_listing"]["price"] == 30_000
	assert listing.provenance["price"].kind == "source_fact"
	assert listing.provenance["price"].source_path == "price"


def test_legacy_factory_converts_listing_child_records_to_models():
	listing = listing_from_legacy({
		"id": "listing-1",
		"seller": {"dealer_fees": [["Doc fee", 250, False]]},
		"installed_addons": {
			"items": [{"name": "Floor Mats", "price": 150}],
			"total": 150,
		},
		"price_history": [{"date": "2026-01-01", "price": 25_000}],
		"warranty": {
			"coverages": [{"type": "Basic", "status": "Active"}],
			"overall_status": "Active",
		},
		"provenance": {"price": {"kind": "source_fact", "api_path": "price"}},
		"warnings": [{
			"field": "mileage", "code": "missing_data", "message": "Missing",
		}],
	})

	assert isinstance(listing.seller.dealer_fees[0], DealerFee)
	assert listing.installed_options is not None
	assert listing.price_history is not None
	assert isinstance(listing.installed_options[0], InstalledOption)
	assert isinstance(listing.price_history[0], PriceHistoryRecord)
	assert isinstance(listing.warranty_coverages[0], WarrantyCoverage)
	assert isinstance(listing.provenance["price"], SourceProvenance)
	assert isinstance(listing.warnings[0], DataWarning)
	assert listing.to_dict()["warranty"]["overall_status"] == "Active"


def test_legacy_serialization_uses_json_compatible_currency_values():
	listing = listing_from_legacy({
		"id": "listing-1",
		"price": 25_500,
		"msrp": 27_000.50,
	})

	serialized = listing.to_dict()

	assert serialized["price"] == 25_500
	assert serialized["msrp"] == 27_000.5
	assert not isinstance(serialized["price"], Decimal)


def test_malformed_child_record_is_reported_instead_of_silently_skipped():
	listing = listing_from_legacy({
		"id": "listing-1",
		"installed_addons": {"items": ["not-an-option"]},
	})

	assert listing.installed_options == []
	assert listing.warnings[-1].field == "installed_addons.items"
	assert listing.warnings[-1].code == "incompatible_data"
