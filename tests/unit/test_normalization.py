from analysis.normalization import (
	best_kbb_model_match,
	filter_valid_listings,
	get_variant_map,
	model_variant_title,
)


def test_model_match_uses_exact_base_unless_specialized_tokens_are_present():
	candidates = ["Prius", "Prius Plug-in Hybrid"]

	assert best_kbb_model_match(
		"Toyota",
		"Prius",
		{"trim": "LE", "trim_version": "LE FWD", "dealer_listing": ""},
		candidates,
	) == "Prius"
	assert best_kbb_model_match(
		"Toyota",
		"Prius",
		{
			"trim": "XSE",
			"trim_version": "XSE Plug-in Hybrid",
			"dealer_listing": "",
		},
		candidates,
	) == "Prius Plug-in Hybrid"
	assert best_kbb_model_match(
		"Toyota",
		"Prius",
		{
			"trim": "SE",
			"trim_version": "",
			"dealer_listing": "https://dealer.example/vehicle/123",
			"fuel_type": "plug-in hybrid",
			"powertrain_type": "phev",
			"body_style": "hatchback",
			"is_plugin": True,
		},
		candidates,
	) == "Prius Plug-in Hybrid"


def test_model_variant_title_corrects_display_value_only_once():
	assert model_variant_title(
		"2025 Toyota Prius SE", "Prius", "Prius Plug-in Hybrid"
	) == "2025 Toyota Prius Plug-in Hybrid SE"
	assert model_variant_title(
		"2025 Toyota Prius Plug-in Hybrid SE",
		"Prius",
		"Prius Plug-in Hybrid",
	) == "2025 Toyota Prius Plug-in Hybrid SE"


def test_model_match_distinguishes_ioniq_n_from_exact_base():
	candidates = ["IONIQ 5", "IONIQ 5 N"]

	assert best_kbb_model_match(
		"Hyundai",
		"IONIQ 5",
		{"trim": "SEL", "trim_version": "SEL AWD", "dealer_listing": ""},
		candidates,
	) == "IONIQ 5"
	assert best_kbb_model_match(
		"Hyundai",
		"IONIQ 5",
		{"trim": "N", "trim_version": "N AWD", "dealer_listing": ""},
		candidates,
	) == "IONIQ 5 N"


async def test_variant_map_keeps_base_and_specialized_models_separate(monkeypatch):
	monkeypatch.setattr(
		"analysis.normalization.load_cache",
		lambda _path: {"2025": {"Toyota": ["Prius", "Prius Plug-in Hybrid"]}},
	)
	base = {
		"id": "base",
		"year": 2025,
		"trim": "LE",
		"trim_version": "LE FWD",
		"dealer_listing": "",
	}
	plugin = {
		"id": "plugin",
		"year": 2025,
		"trim": "XSE",
		"trim_version": "XSE Plug-in Hybrid",
		"dealer_listing": "",
	}

	variants = await get_variant_map("Toyota", "Prius", [base, plugin])

	assert variants == {
		"2025 Toyota Prius": [base],
		"2025 Toyota Prius Plug-in Hybrid": [plugin],
	}


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


def test_filter_does_not_collapse_specialty_trim_to_partial_identity():
	listing = {
		"id": "standard-range",
		"year": 2025,
		"trim": "SE Standard Range",
		"price": 25_000,
	}
	failed_key = "2025 Hyundai IONIQ 5 SE Standard Range"
	entries = {
		"2025 Hyundai IONIQ 5 SE": {
			"natl_source": "https://kbb.com/hyundai/ioniq-5/2025/",
		},
		failed_key: {
			"natl_source": "https://kbb.com/hyundai/ioniq-5/2025/",
			"skip_reason": "Pricing unavailable.",
		},
	}
	variant_map = {"2025 Hyundai IONIQ 5": [listing]}

	valid, skipped, _ = filter_valid_listings(
		"Hyundai", "IONIQ 5", [listing], entries, variant_map
	)

	assert valid == []
	assert skipped == [listing]
