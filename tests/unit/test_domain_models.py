import json

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from deal_lens.models import (
	DataWarning,
	DealerFee,
	KBBNationalTable,
	KBBFPPBasis,
	KBBFreshness,
	KBBPricingBasis,
	KBBPricingCache,
	KBBPricingEntry,
	Listing,
	ListingCondition,
	ListingDataset,
	ListingEvaluation,
	ResourceState,
	SupplementaryResourceStatus,
	SupplementaryStatus,
	Vehicle,
)


def test_listing_evaluation_composes_listing_facts_and_pricing():
	listing = Listing(
		id="listing-1",
		title="2025 Ford F-150 XLT",
		vehicle=Vehicle(
			vin="VIN123", year=2025, make="Ford", model="F-150",
			trim_version="SuperCrew",
		),
		condition=ListingCondition.NEW,
		price=Decimal("42000"),
		mileage=12,
	)
	evaluation = ListingEvaluation(
		listing=listing,
		cache_key="2025 Ford F-150 XLT",
		base_trim="XLT",
		pricing=KBBPricingEntry(msrp=45_000, fpp_local=43_000),
		price_delta=-1_000,
		deal_rating="Good",
	)

	assert evaluation.id == listing.id
	assert evaluation.make == "Ford"
	assert evaluation.fpp_local == 43_000
	assert evaluation.to_dict()["deal_rating"] == "Good"


def test_kbb_pricing_cache_models_incomplete_and_empty_results():
	now = datetime.now().isoformat()
	payload = {
		"level23_entries": {
			"entry": {
				"model": "F150 SuperCrew Cab",
				"kbb_trim": "entry",
				"fpp_local": 44_880,
				"pricing_basis": "vin",
				"provider_note": "preserved",
			},
		},
		"configurations": {
			"fingerprint": {
				"year": "2025", "make": "Ford", "model": "F150 SuperCrew Cab",
				"style": "XLT Pickup 4D", "style_url": "https://kbb.example/style",
				"cache_key": "entry",
			},
		},
		"vin_resolutions": {
			"VIN": {"configuration": "fingerprint", "timestamp": now},
		},
		"level23_national_tables": {
			"2025 Ford F150 SuperCrew Cab": {"timestamp": now, "rows": []},
		},
		"pricing_lookups": {
			"2025 Ford F150 SuperCrew Cab": {"status": "complete", "checked_at": now},
		},
		"future_section": {"kept": True},
	}

	cache = KBBPricingCache.from_dict(payload)

	assert cache.level23_entries["entry"].pricing_basis is KBBPricingBasis.VIN
	assert cache.level23_entries["entry"].fpp_vin_local == 44_880
	assert cache.level23_entries["entry"].msrp is None
	assert cache.level23_entries["entry"].extra["provider_note"] == "preserved"
	assert cache.national_tables["2025 Ford F150 SuperCrew Cab"].rows == ()
	serialized = cache.to_dict()
	assert serialized["level23_national_tables"]["2025 Ford F150 SuperCrew Cab"]["rows"] == []
	assert serialized["future_section"] == {"kept": True}


def test_kbb_fpp_selector_preserves_precedence_and_provenance():
	now = datetime.now()
	entry = KBBPricingEntry(
		fpp_vin_local=31_000,
		vin_local_source="https://kbb.example/vin",
		vin_local_timestamp=now,
		fpp_table_local=30_500,
		table_local_source="https://kbb.example/table-trim",
		table_local_timestamp=now,
		fpp_natl=30_000,
		natl_source="https://kbb.example/table",
		natl_timestamp=now,
	)

	anchor = entry.selected_fpp_anchor(timedelta(days=1), now=now)

	assert anchor is not None
	assert anchor.value == 31_000
	assert anchor.basis is KBBFPPBasis.VIN_LOCAL
	assert anchor.source_url == "https://kbb.example/vin"
	assert anchor.timestamp == now
	assert anchor.freshness is KBBFreshness.FRESH


def test_kbb_fpp_selector_skips_stale_higher_priority_values():
	now = datetime.now()
	entry = KBBPricingEntry(
		fpp_vin_local=31_000,
		vin_local_source="https://kbb.example/vin",
		vin_local_timestamp=now - timedelta(days=2),
		fpp_table_local=30_500,
		table_local_source="https://kbb.example/table-trim",
		table_local_timestamp=now,
		fpp_natl=30_000,
		natl_source="https://kbb.example/table",
		natl_timestamp=now,
	)

	anchor = entry.selected_fpp_anchor(timedelta(days=1), now=now)

	assert anchor is not None
	assert anchor.basis is KBBFPPBasis.TABLE_LOCAL
	assert anchor.value == 30_500
	assert anchor.uncertainty == ("vin_local FPP is stale",)


def test_kbb_fpp_selector_uses_national_after_stale_local_values():
	now = datetime.now()
	entry = KBBPricingEntry(
		fpp_vin_local=31_000,
		vin_local_timestamp=now - timedelta(days=2),
		fpp_table_local=30_500,
		table_local_timestamp=now - timedelta(days=2),
		fpp_natl=30_000,
		natl_source="https://kbb.example/table",
		natl_timestamp=now,
	)

	anchor = entry.selected_fpp_anchor(timedelta(days=1), now=now)

	assert anchor is not None
	assert anchor.basis is KBBFPPBasis.NATIONAL
	assert anchor.value == 30_000
	assert anchor.source_url == "https://kbb.example/table"


def test_kbb_legacy_local_migration_does_not_guess_ambiguous_provenance():
	known = KBBPricingEntry.from_dict({
		"fpp_local": 31_000,
		"local_source": "https://kbb.example/vin",
		"pricing_basis": "vin",
	})
	ambiguous = KBBPricingEntry.from_dict({
		"fpp_local": 30_500,
		"local_source": "https://kbb.example/unknown",
	})

	assert known.fpp_vin_local == 31_000
	assert known.vin_local_source == "https://kbb.example/vin"
	assert ambiguous.fpp_vin_local is None
	assert ambiguous.fpp_table_local is None
	assert ambiguous.fpp_local == 30_500
	assert ambiguous.uncertainty is not None
	assert ambiguous.selected_fpp_anchor() is None


def test_kbb_national_table_rejects_missing_timestamp():
	try:
		KBBNationalTable.from_dict({"rows": []})
	except ValueError as error:
		assert "timestamp" in str(error)
	else:
		raise AssertionError("missing timestamp should be rejected")


def test_supplementary_resource_status_round_trips_retry_state():
	attempted = datetime.now()
	status = SupplementaryResourceStatus(
		state=ResourceState.FAILED,
		source_url="https://images.example/vehicle.jpg",
		attempted_at=attempted,
		retry_after=attempted + timedelta(days=1),
		failure_reason="http_error",
		http_status=403,
	)

	restored = SupplementaryResourceStatus.from_dict(status.to_dict())

	assert restored == status


def test_supplementary_status_controls_retries_and_url_changes():
	now = datetime.now()
	status = SupplementaryResourceStatus(
		state=ResourceState.FAILED,
		source_url="https://images.example/old.jpg",
		attempted_at=now,
		retry_after=now + timedelta(days=1),
		failure_reason="timeout",
	)
	supplementary = SupplementaryStatus(image=status)

	assert not supplementary.should_attempt(
		"image", "https://images.example/old.jpg", now=now
	)
	assert supplementary.should_attempt(
		"image", "https://images.example/new.jpg", now=now
	)
	assert supplementary.should_attempt(
		"image", "https://images.example/old.jpg", now=now + timedelta(days=2)
	)


def test_dealer_fee_accepts_legacy_tuple_and_mapping():
	legacy = DealerFee.from_legacy(("Documentation fee", 599, False, "Dealer fee"))
	mapping = DealerFee.from_legacy({"name": "Delivery", "amount": 250, "included": True})

	assert legacy.to_legacy() == ("Documentation fee", 599.0, False, "Dealer fee")
	assert mapping.amount == 250
	assert mapping.included is True


def test_data_warning_preserves_provider_specific_fields():
	warning = DataWarning.from_dict({
		"code": "missing_data", "field": "price", "message": "missing",
		"source": "visor_api", "api_path": "price", "received_type": "null",
	})

	assert warning.source_path == "price"
	assert warning.to_dict()["received_type"] == "null"


def test_listing_dataset_loads_recorded_shape_and_round_trips_unknown_sections():
	fixture = Path(__file__).parents[2] / "output" / "example_output.json"
	payload = json.loads(fixture.read_text(encoding="utf-8"))
	# The small historical example predates runtime metadata; supply the required
	# boundary field while retaining every original section.
	payload.setdefault("metadata", {}).setdefault("runtime", {"timestamp": "example"})
	payload["future_section"] = {"schema": 2}

	dataset = ListingDataset.from_dict(payload)
	serialized = dataset.to_dict()

	assert len(dataset.listings) == len(payload["listings"])
	assert serialized["future_section"] == {"schema": 2}
	assert serialized["metadata"]["runtime"]["timestamp"] == payload["metadata"]["runtime"]["timestamp"]


def test_kbb_cache_preserves_unavailable_vin_resolution():
	payload = {
		"vin_resolutions": {
			"VIN": {
				"configuration": None,
				"status": "unavailable",
				"timestamp": datetime.now().isoformat(),
			},
		},
	}

	cache = KBBPricingCache.from_dict(payload)
	resolution = cache.vin_resolutions["VIN"]

	assert resolution.configuration is None
	assert resolution.status == "unavailable"
	assert cache.to_dict()["vin_resolutions"]["VIN"]["status"] == "unavailable"


def test_kbb_cache_owns_freshness_and_negative_result_rules():
	now = datetime.now()
	cache = KBBPricingCache.from_dict({
		"level23_entries": {
			"entry": {
				"local_timestamp": now.isoformat(),
				"natl_timestamp": now.isoformat(),
			},
		},
		"vin_resolutions": {
			"VIN": {
				"configuration": None,
				"status": "unavailable",
				"timestamp": now.isoformat(),
			},
		},
		"level23_national_tables": {
			"vehicle": {"timestamp": now.isoformat(), "rows": []},
		},
	})
	ttl = timedelta(days=1)

	entry = cache.level23_entry("entry")
	assert entry is not None
	assert entry.is_complete_and_fresh(ttl)
	assert cache.vin_unavailable_is_fresh("VIN", ttl)
	assert cache.fresh_national_rows("vehicle", ttl) == ()


def test_kbb_cache_selects_configurations_by_vehicle_identity():
	cache = KBBPricingCache.from_dict({
		"configurations": {
			"match": {
				"year": "2025", "make": "Ford", "model": "F150",
				"style": "XLT", "style_url": "https://example.test/xlt",
				"cache_key": "entry",
			},
			"other": {
				"year": "2024", "make": "Ford", "model": "F150",
				"style": "XLT", "style_url": "https://example.test/xlt",
				"cache_key": "other-entry",
			},
		},
	})

	assert list(cache.configurations_for(
		year="2025", make="ford", model="f150"
	)) == ["match"]
