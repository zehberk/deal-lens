import json

from datetime import datetime, timedelta
from pathlib import Path

from deal_lens.models import (
	DataWarning,
	DealerFee,
	KBBNationalTable,
	KBBPricingBasis,
	KBBPricingCache,
	ListingDataset,
	ResourceState,
	SupplementaryResourceStatus,
)


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
	assert cache.level23_entries["entry"].msrp is None
	assert cache.level23_entries["entry"].extra["provider_note"] == "preserved"
	assert cache.national_tables["2025 Ford F150 SuperCrew Cab"].rows == ()
	serialized = cache.to_dict()
	assert serialized["level23_national_tables"]["2025 Ford F150 SuperCrew Cab"]["rows"] == []
	assert serialized["future_section"] == {"kept": True}


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
