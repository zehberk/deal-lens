from analysis.normalization import filter_valid_listings


def _entry(*, skip_reason: str | None = None) -> dict:
	entry = {
		"natl_source": "https://kbb.com/hyundai/ioniq-5/2024/",
	}
	if skip_reason:
		entry["skip_reason"] = skip_reason
	return entry


def test_filter_prefers_usable_base_trim_over_failed_detailed_lookup():
	listing = {
		"id": "sel-rwd",
		"year": 2024,
		"trim": "SEL",
		"trim_version": "SEL RWD",
		"price": 25_000,
	}
	priced_key = "2024 Hyundai IONIQ 5 SEL Sport Utility 4D"
	failed_key = "2024 Hyundai IONIQ 5 sel rwd"
	entries = {
		priced_key: _entry(),
		failed_key: _entry(skip_reason="Pricing unavailable."),
	}
	variant_map = {"2024 Hyundai IONIQ 5": [listing]}

	valid, skipped, _ = filter_valid_listings(
		"Hyundai", "IONIQ 5", [listing], entries, variant_map
	)

	assert skipped == []
	assert valid[0]["cache_key"] == priced_key


def test_filter_keeps_failed_lookup_when_no_usable_trim_identity_matches():
	listing = {
		"id": "unknown",
		"year": 2024,
		"trim": "Unknown Edition",
		"price": 25_000,
	}
	failed_key = "2024 Hyundai IONIQ 5 Unknown Edition"
	entries = {
		"2024 Hyundai IONIQ 5 SEL Sport Utility 4D": _entry(),
		failed_key: _entry(skip_reason="Pricing unavailable."),
	}
	variant_map = {"2024 Hyundai IONIQ 5": [listing]}

	valid, skipped, _ = filter_valid_listings(
		"Hyundai", "IONIQ 5", [listing], entries, variant_map
	)

	assert valid == []
	assert skipped == [listing]
