import json

from datetime import datetime

import pytest

from utils.cache import (
	CacheLoadError,
	cache_covers_all,
	is_pricing_lookup_fresh,
	load_cache,
	record_pricing_lookup,
	save_cache,
)
from utils.constants import KBB_CACHE_TTL


def test_load_cache_rejects_malformed_existing_file(tmp_path):
	cache_path = tmp_path / "pricing.cache"
	cache_path.write_text('{"entries":', encoding="utf-8")

	with pytest.raises(CacheLoadError, match="will not be overwritten"):
		load_cache(cache_path)

	assert cache_path.read_text(encoding="utf-8") == '{"entries":'


def test_load_cache_accepts_utf8_bom(tmp_path):
	cache_path = tmp_path / "pricing.cache"
	cache_path.write_text(
		json.dumps({"entries": {"preserved": {}}}), encoding="utf-8-sig"
	)

	assert load_cache(cache_path) == {"entries": {"preserved": {}}}


def test_save_cache_atomically_replaces_file_and_keeps_backup(tmp_path):
	cache_path = tmp_path / "pricing.cache"
	original = {"entries": {"old": {"fpp_local": 20_000}}}
	updated = {"entries": {"new": {"fpp_local": 25_000}}}
	cache_path.write_text(json.dumps(original), encoding="utf-8")

	save_cache(updated, cache_path)

	assert load_cache(cache_path) == updated
	assert load_cache(tmp_path / "pricing.cache.bak") == original
	assert list(tmp_path.glob(".pricing.cache.*.tmp")) == []


def test_save_cache_serialization_failure_preserves_existing_file(tmp_path):
	cache_path = tmp_path / "pricing.cache"
	original_text = json.dumps({"entries": {"old": {}}})
	cache_path.write_text(original_text, encoding="utf-8")

	with pytest.raises(TypeError):
		save_cache({"not_json": object()}, cache_path)

	assert cache_path.read_text(encoding="utf-8") == original_text
	assert not (tmp_path / "pricing.cache.bak").exists()
	assert list(tmp_path.glob(".pricing.cache.*.tmp")) == []


def test_save_cache_removes_legacy_model_slugs_after_success(tmp_path):
	cache_path = tmp_path / "pricing.cache"
	cache = {
		"entries": {},
		"model_slugs": {"2025 Honda Civic": "civic"},
	}

	save_cache(cache, cache_path)

	assert "model_slugs" not in cache
	assert "model_slugs" not in load_cache(cache_path)


def test_failed_save_keeps_legacy_model_slugs_in_memory(tmp_path):
	cache = {
		"model_slugs": {"2025 Honda Civic": "civic"},
		"not_json": object(),
	}

	with pytest.raises(TypeError):
		save_cache(cache, tmp_path / "pricing.cache")

	assert cache["model_slugs"] == {"2025 Honda Civic": "civic"}


def test_recorded_lookup_covers_an_empty_pricing_result():
	model_key = "2025 Honda Civic"
	cache = {"entries": {}}

	record_pricing_lookup(cache, model_key)

	assert is_pricing_lookup_fresh(cache, model_key)
	assert cache_covers_all([model_key], {"2025": {}}, cache)


def test_stale_or_malformed_lookup_does_not_cover_pricing():
	model_key = "2025 Honda Civic"
	stale = (datetime.now() - KBB_CACHE_TTL).isoformat()
	cache = {
		"entries": {},
		"pricing_lookups": {
			model_key: {"status": "complete", "checked_at": stale},
		},
	}

	assert not is_pricing_lookup_fresh(cache, model_key)
	assert not cache_covers_all([model_key], {"2025": {}}, cache)
	cache["pricing_lookups"][model_key] = "invalid"
	assert not is_pricing_lookup_fresh(cache, model_key)
